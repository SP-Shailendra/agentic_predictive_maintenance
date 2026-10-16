import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from core.audit import append_workflow_event
from core.config import settings
from core.database import SessionLocal
from core.security import Principal
from models import WorkOrder
from services.cmms_service import fetch_work_order_status, map_cmms_status

logger = logging.getLogger(__name__)
STATUS_WORKER_PRINCIPAL = Principal(
    subject="cmms-status-worker",
    roles=frozenset({"service"}),
    verified=False,
    auth_method="service",
)
CLAIM_BATCH_SIZE = 20


async def _claim_due_orders() -> list[tuple[str, str]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.cmms_status_poll_seconds)
    async with SessionLocal() as session:
        async with session.begin():
            orders = await session.scalars(
                select(WorkOrder)
                .where(
                    WorkOrder.status.in_(("dispatched", "in_progress")),
                    WorkOrder.cmms_reference.is_not(None),
                    ~WorkOrder.cmms_reference.like("SIM-%"),
                    or_(WorkOrder.cmms_status_checked_at.is_(None), WorkOrder.cmms_status_checked_at <= cutoff),
                )
                .order_by(WorkOrder.cmms_status_checked_at, WorkOrder.id)
                .limit(CLAIM_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            claimed = []
            for order in orders.all():
                order.cmms_status_checked_at = now
                claimed.append((str(order.id), str(order.cmms_reference)))
            return claimed


async def _record_external_status(work_order_id: str, external_status: str) -> None:
    mapped_status = map_cmms_status(external_status)
    async with SessionLocal() as session:
        async with session.begin():
            order = await session.scalar(
                select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
            )
            if order is None:
                return
            previous_external = order.cmms_external_status
            previous_status = order.status
            order.cmms_external_status = external_status
            permitted = {
                "dispatched": {"in_progress", "completed"},
                "in_progress": {"completed"},
            }
            if mapped_status in permitted.get(order.status, set()):
                order.status = mapped_status
                state = dict(order.workflow_state or {})
                state["status"] = mapped_status
                state["cmms_external_status"] = external_status
                order.workflow_state = state
                now = datetime.now(timezone.utc)
                if mapped_status == "in_progress" and order.in_progress_at is None:
                    order.in_progress_at = now
                if mapped_status == "completed" and order.completed_at is None:
                    order.completed_at = now
            if previous_external != external_status or order.status != previous_status:
                await append_workflow_event(
                    session,
                    order,
                    "cmms_status_synchronized",
                    previous_status,
                    {"external_status": external_status, "mapped_status": mapped_status},
                    STATUS_WORKER_PRINCIPAL,
                )


async def process_due_status_sync() -> int:
    if not settings.cmms_status_url:
        return 0
    claimed = await _claim_due_orders()
    for work_order_id, reference in claimed:
        try:
            status = await fetch_work_order_status(reference)
            await _record_external_status(work_order_id, status)
        except Exception as error:
            logger.warning("CMMS status sync failed for work order (%s)", type(error).__name__)
    return len(claimed)


async def cmms_status_worker_loop() -> None:
    while True:
        try:
            await process_due_status_sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CMMS status worker iteration failed")
        await asyncio.sleep(settings.cmms_status_poll_seconds)