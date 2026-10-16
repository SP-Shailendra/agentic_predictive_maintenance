from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from core.config import settings

_tracer_provider: TracerProvider | None = None


def instrument_fastapi(app: FastAPI) -> bool:
    global _tracer_provider
    if not settings.otel_exporter_otlp_endpoint:
        return False
    if _tracer_provider is None:
        _tracer_provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name}),
        )
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
        trace.set_tracer_provider(_tracer_provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer_provider)
    return True


def shutdown_telemetry() -> None:
    global _tracer_provider
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
        _tracer_provider = None