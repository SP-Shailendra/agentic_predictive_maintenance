import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import append_workflow_event
from core.database import SessionLocal
from core.security import Principal
from models import Asset, CMMSOutbox, WorkOrder
from services.cmms_service import dispatch_work_order
from services.workflow_notifications import enqueue_workflow_notification

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
LOCK_LEASE = timedelta(minutes=2)
WORKER_PRINCIPAL = Principal(
    subject="cmms-outbox-worker",
    roles=frozenset({"service"}),
    verified=False,
    auth_method="service",
)


async def _claim_due_item() -> tuple[str, str, dict[str, object], int] | None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        async with session.begin():
            ready = and_(
                CMMSOutbox.status.in_(("queued", "retry")),
                CMMSOutbox.next_attempt_at <= now,
            )
            expired_lease = and_(
                CMMSOutbox.status == "sending",
                CMMSOutbox.locked_at <= now - LOCK_LEASE,
            )
            item = await session.scalar(
                select(CMMSOutbox)
                .where(or_(ready, expired_lease))
                .order_by(CMMSOutbox.created_at, CMMSOutbox.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if item is None:
                return None
            item.status = "sending"
            item.attempts += 1
            item.locked_at = now
            work_order = await session.scalar(
                select(WorkOrder).where(WorkOrder.id == item.work_order_id).with_for_update()
            )
            if work_order is not None:
                previous_status = work_order.status
                work_order.status = "dispatching"
                workflow_state = dict(work_order.workflow_state)
                workflow_state["status"] = "dispatching"
                work_order.workflow_state = workflow_state
                await append_workflow_event(
                    session,
                    work_order,
                    "cmms_dispatch_started",
                    previous_status,
                    {"attempt": item.attempts},
                    WORKER_PRINCIPAL,
                )
            await session.flush()
            return str(item.id), str(item.work_order_id), dict(item.payload), item.attempts


async def _record_failure(outbox_id: str, work_order_id: str, attempts: int, error: Exception) -> None:
    now = datetime.now(timezone.utc)
    terminal = attempts >= MAX_ATTEMPTS
    delay_seconds = min(3600, 2 ** min(attempts, 11))
    safe_error = type(error).__name__
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        safe_error = f"{safe_error} (HTTP {status_code})"

    async with SessionLocal() as session:
        async with session.begin():
            outbox = await session.scalar(
                select(CMMSOutbox).where(CMMSOutbox.id == outbox_id).with_for_update()
            )
            work_order = await session.scalar(
                select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
            )
            if outbox is None or work_order is None or outbox.status == "sent":
                return
            previous_status = work_order.status
            outbox.status = "failed" if terminal else "retry"
            outbox.locked_at = None
            outbox.last_error = safe_error[:500]
            outbox.next_attempt_at = now + timedelta(seconds=delay_seconds)
            work_order.status = "dispatch_failed" if terminal else "dispatch_retry"
            workflow_state = dict(work_order.workflow_state)
            workflow_state["status"] = work_order.status
            work_order.workflow_state = workflow_state
            await append_workflow_event(
                session,
                work_order,
                "cmms_dispatch_failed" if terminal else "cmms_dispatch_retry",
                previous_status,
                {"attempt": attempts, "error": safe_error[:500], "terminal": terminal},
                WORKER_PRINCIPAL,
            )
            if terminal:
                asset = await session.get(Asset, work_order.asset_id)
                await enqueue_workflow_notification(
                    session,
                    work_order,
                    "dispatch_failed",
                    asset.asset_tag if asset else str(work_order.asset_id),
                )


async def _record_success(outbox_id: str, work_order_id: str, reference: str) -> None:
    async with SessionLocal() as session:
        async with session.begin():
            outbox = await session.scalar(
                select(CMMSOutbox).where(CMMSOutbox.id == outbox_id).with_for_update()
            )
            work_order = await session.scalar(
                select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
            )
            if outbox is None or work_order is None or outbox.status == "sent":
                return
            previous_status = work_order.status
            outbox.status = "sent"
            outbox.cmms_reference = reference
            outbox.locked_at = None
            outbox.last_error = None
            work_order.status = "dispatched"
            work_order.cmms_reference = reference
            work_order.dispatched_at = datetime.now(timezone.utc)
            workflow_state = dict(work_order.workflow_state)
            workflow_state["status"] = "dispatched"
            workflow_state["cmms_reference"] = reference
            workflow_state["activity"] = [
                *workflow_state.get("activity", []),
                {"agent": "Dispatch", "message": f"Work order {reference} accepted by the configured CMMS boundary.", "status": "complete"},
            ]
            work_order.workflow_state = workflow_state
            await append_workflow_event(
                session,
                work_order,
                "cmms_dispatch_succeeded",
                previous_status,
                {"reference": reference, "attempts": outbox.attempts},
                WORKER_PRINCIPAL,
            )


async def process_one_outbox_item() -> bool:
    claimed = await _claim_due_item()
    if claimed is None:
        return False
    outbox_id, work_order_id, payload, attempts = claimed
    try:
        reference = await dispatch_work_order(payload)
    except Exception as error:
        logger.warning("CMMS dispatch attempt %s failed (%s)", attempts, type(error).__name__)
        await _record_failure(outbox_id, work_order_id, attempts, error)
    else:
        await _record_success(outbox_id, work_order_id, reference)
    return True


async def outbox_worker_loop(poll_interval_seconds: float = 1.0) -> None:
    while True:
        try:
            processed = await process_one_outbox_item()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CMMS outbox worker iteration failed")
            await asyncio.sleep(min(5.0, poll_interval_seconds))
        else:
            if not processed:
                await asyncio.sleep(poll_interval_seconds)