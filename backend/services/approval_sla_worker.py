import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.audit import append_workflow_event
from core.config import settings
from core.database import SessionLocal
from core.security import Principal
from models import Asset, WorkOrder, WorkflowEvent
from services.workflow_notifications import enqueue_workflow_notification

logger = logging.getLogger(__name__)
SLA_PRINCIPAL = Principal(
    subject="approval-sla-worker",
    roles=frozenset({"service"}),
    verified=False,
    auth_method="service",
)


async def flag_one_overdue_approval() -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.approval_sla_minutes)
    overdue_event_exists = select(WorkflowEvent.id).where(
        WorkflowEvent.event_type == "approval_overdue",
        WorkflowEvent.work_order_id == WorkOrder.id,
    ).exists()
    async with SessionLocal() as session:
        async with session.begin():
            order = await session.scalar(
                select(WorkOrder)
                .where(WorkOrder.status == "awaiting_approval", WorkOrder.created_at <= cutoff, ~overdue_event_exists)
                .order_by(WorkOrder.created_at, WorkOrder.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if order is None:
                return False
            state = dict(order.workflow_state)
            state["approval_overdue"] = True
            state["approval_overdue_at"] = now.isoformat()
            order.workflow_state = state
            await append_workflow_event(
                session,
                order,
                "approval_overdue",
                "awaiting_approval",
                {"sla_minutes": settings.approval_sla_minutes},
                SLA_PRINCIPAL,
            )
            asset_tag = str(state.get("asset_tag") or order.asset_id)
            asset = await session.get(Asset, order.asset_id)
            if asset is not None:
                asset_tag = asset.asset_tag
            await enqueue_workflow_notification(session, order, "approval_overdue", asset_tag)
    return True


async def approval_sla_worker_loop(poll_interval_seconds: float = 5.0) -> None:
    while True:
        try:
            processed = await flag_one_overdue_approval()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Approval SLA worker iteration failed")
            await asyncio.sleep(min(10.0, poll_interval_seconds))
        else:
            if not processed:
                await asyncio.sleep(poll_interval_seconds)