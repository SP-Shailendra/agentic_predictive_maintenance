from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import NotificationOutbox, WorkOrder


def workflow_deep_link(work_order_id: UUID | str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}#workflow/{work_order_id}"


async def enqueue_workflow_notification(
    db: AsyncSession,
    work_order: WorkOrder,
    event_type: str,
    asset_tag: str,
) -> None:
    status = work_order.status
    payload = {
        "asset_tag": asset_tag,
        "status": status,
        "url": workflow_deep_link(work_order.id),
    }
    channels: list[tuple[str, str]] = []
    if settings.smtp_host and settings.smtp_from and settings.notification_email_to:
        channels.append(("email", settings.notification_email_to))
    if settings.notification_webhook_url:
        channels.append(("webhook", "configured-webhook"))

    for channel, recipient_key in channels:
        dedupe_key = f"{work_order.id}:{event_type}:{channel}"
        await db.execute(
            insert(NotificationOutbox)
            .values(
                work_order_id=work_order.id,
                event_type=event_type,
                channel=channel,
                dedupe_key=dedupe_key,
                payload={**payload, "recipient_key": recipient_key},
                status="queued",
                attempts=0,
            )
            .on_conflict_do_nothing(constraint="uq_notification_outbox_channel_dedupe")
        )