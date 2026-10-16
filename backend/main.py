import asyncio
import secrets
from collections import deque
from time import perf_counter
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from agents.edge_simulator import seed_demo_assets, simulator_loop
from core.config import settings
from core.database import engine
from core.migrations import upgrade_database
from core.observability import instrument_fastapi, shutdown_telemetry
from routers import assets, chat, knowledge, plants, simulator, telemetry, workflow
from services.rag_service import initialize_knowledge_store
from services.outbox_worker import outbox_worker_loop
from services.approval_sla_worker import approval_sla_worker_loop
from services.notification_worker import notification_worker_loop
from services.cmms_status_worker import cmms_status_worker_loop
from services.document_ingestion_worker import document_ingestion_worker_loop


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(upgrade_database)
    await initialize_knowledge_store()
    await seed_demo_assets()
    if settings.telemetry_retention_days > 0:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT add_retention_policy(" 
                    "'sensor_data', make_interval(days => :retention_days), if_not_exists => TRUE)"
                ),
                {"retention_days": settings.telemetry_retention_days},
            )
    simulator_task = None
    if settings.simulator_enabled:
        simulator_task = asyncio.create_task(simulator_loop(settings.simulator_interval_seconds))
    outbox_task = asyncio.create_task(outbox_worker_loop(), name="cmms-outbox-worker")
    sla_task = asyncio.create_task(approval_sla_worker_loop(), name="approval-sla-worker")
    notification_task = asyncio.create_task(notification_worker_loop(), name="notification-outbox-worker")
    cmms_status_task = asyncio.create_task(cmms_status_worker_loop(), name="cmms-status-worker")
    document_ingestion_task = asyncio.create_task(document_ingestion_worker_loop(), name="knowledge-ingestion-worker")
    try:
        yield
    finally:
        for task in (simulator_task, outbox_task, sla_task, notification_task, cmms_status_task, document_ingestion_task):
            if task:
                task.cancel()
        for task in (simulator_task, outbox_task, sla_task, notification_task, cmms_status_task, document_ingestion_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await engine.dispose()
        shutdown_telemetry()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Human-approved agentic condition monitoring and maintenance workflow API.",
    lifespan=lifespan,
)
instrument_fastapi(app)
app.include_router(assets.router, prefix="/api")
app.include_router(plants.router, prefix="/api")
app.include_router(telemetry.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(simulator.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")

_rate_limit_buckets: dict[str, deque[float]] = {}
_rate_limit_lock = asyncio.Lock()
_rate_limit_window_seconds = 60.0


@app.middleware("http")
async def limit_request_rate(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/health", "/ready", "/metrics"}:
        return await call_next(request)
    client_key = request.client.host if request.client else "unknown"
    now = perf_counter()
    async with _rate_limit_lock:
        bucket = _rate_limit_buckets.setdefault(client_key, deque())
        while bucket and now - bucket[0] >= _rate_limit_window_seconds:
            bucket.popleft()
        if len(bucket) >= settings.rate_limit_requests_per_minute:
            retry_after = max(1, int(_rate_limit_window_seconds - (now - bucket[0])))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Request rate limit exceeded."},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        if len(_rate_limit_buckets) > 5000:
            for stale_key in list(_rate_limit_buckets)[:1000]:
                if stale_key != client_key:
                    _rate_limit_buckets.pop(stale_key, None)
    return await call_next(request)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str | bool | float]:
    return {
        "status": "ok",
        "service": "agentic-predictive-maintenance",
        "simulator_enabled": settings.simulator_enabled,
        "default_vibration_threshold_mm_s": settings.default_vibration_threshold_mm_s,
        "default_temperature_threshold_c": settings.default_temperature_threshold_c,
    }


@app.get("/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        ) from exc
    return {"status": "ready", "service": "agentic-predictive-maintenance"}


REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP requests handled by the API.",
    ("method", "route", "status"),
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next):
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUEST_COUNT.labels(request.method, route, "500").inc()
        REQUEST_DURATION.labels(request.method, route).observe(perf_counter() - started_at)
        raise
    route = getattr(request.scope.get("route"), "path", "unmatched")
    REQUEST_COUNT.labels(request.method, route, str(response.status_code)).inc()
    REQUEST_DURATION.labels(request.method, route).observe(perf_counter() - started_at)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


@app.get("/metrics", include_in_schema=False, tags=["health"])
async def metrics(x_metrics_api_key: str | None = Header(default=None)) -> Response:
    if settings.metrics_api_key and not secrets.compare_digest(x_metrics_api_key or "", settings.metrics_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A valid X-Metrics-API-Key is required.")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
