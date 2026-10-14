import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import Principal
from models import WorkOrder, WorkflowEvent


def workflow_event_hash(
    *,
    event_id: str,
    work_order_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    actor_roles: list[str],
    actor_verified: bool,
    from_status: str | None,
    to_status: str | None,
    details: dict[str, Any],
    previous_hash: str | None,
    created_at: datetime,
) -> str:
    payload = {
        "id": event_id,
        "work_order_id": work_order_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "actor_roles": sorted(actor_roles),
        "actor_verified": actor_verified,
        "from_status": from_status,
        "to_status": to_status,
        "details": details,
        "previous_hash": previous_hash,
        "created_at": created_at.isoformat(),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def append_workflow_event(
    db: AsyncSession,
    work_order: WorkOrder,
    event_type: str,
    from_status: str | None,
    details: dict[str, Any],
    principal: Principal,
) -> WorkflowEvent:
    await db.scalar(select(WorkOrder.id).where(WorkOrder.id == work_order.id).with_for_update())
    previous_event = (await db.execute(
        select(WorkflowEvent.event_hash, WorkflowEvent.created_at)
        .where(WorkflowEvent.work_order_id == work_order.id)
        .order_by(WorkflowEvent.created_at.desc(), WorkflowEvent.id.desc())
        .limit(1)
        .with_for_update()
    )).first()
    previous_hash = previous_event.event_hash if previous_event else None
    created_at = datetime.now(timezone.utc)
    if previous_event and previous_event.created_at >= created_at:
        created_at = previous_event.created_at + timedelta(microseconds=1)

    actor_types = {
        "oidc": "oidc_user",
        "api_key": "service_account",
        "development": "development_identity",
        "service": "service_account",
    }
    actor_type = actor_types.get(principal.auth_method, "unknown")
    actor_roles = sorted(principal.roles)
    event_id = uuid.uuid4()
    event_hash = workflow_event_hash(
        event_id=str(event_id),
        work_order_id=str(work_order.id),
        event_type=event_type,
        actor_type=actor_type,
        actor_id=principal.subject,
        actor_roles=actor_roles,
        actor_verified=principal.verified,
        from_status=from_status,
        to_status=work_order.status,
        details=details,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    event = WorkflowEvent(
        id=event_id,
        work_order_id=work_order.id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=principal.subject,
        actor_roles=actor_roles,
        actor_verified=principal.verified,
        from_status=from_status,
        to_status=work_order.status,
        details=details,
        previous_hash=previous_hash,
        event_hash=event_hash,
        created_at=created_at,
    )
    db.add(event)
    await db.flush()
    return event