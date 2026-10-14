import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import and_, or_, select

from core.config import settings
from core.database import SessionLocal
from models import NotificationOutbox

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
LOCK_LEASE = timedelta(minutes=2)


async def _claim_due_item() -> tuple[str, str, str, dict[str, Any], int] | None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        async with session.begin():
            ready = and_(NotificationOutbox.status.in_(("queued", "retry")), NotificationOutbox.next_attempt_at <= now)
            expired = and_(NotificationOutbox.status == "sending", NotificationOutbox.locked_at <= now - LOCK_LEASE)
            item = await session.scalar(
                select(NotificationOutbox)
                .where(or_(ready, expired))
                .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if item is None:
                return None
            item.status = "sending"
            item.attempts += 1
            item.locked_at = now
            return str(item.id), item.channel, item.dedupe_key, dict(item.payload), item.attempts


def _send_email(payload: dict[str, Any], dedupe_key: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = settings.notification_email_to
    message["Subject"] = f"Fieldnote: {payload['status'].replace('_', ' ')}"
    message["Message-ID"] = f"<{dedupe_key.replace(':', '.')}.fieldnote>"
    message.set_content(
        f"Asset: {payload['asset_tag']}\nStatus: {payload['status']}\nReview: {payload['url']}\n"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        client.ehlo()
        if settings.smtp_starttls:
            client.starttls()
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)


def build_webhook_payload(payload: dict[str, Any], format_name: str) -> dict[str, Any]:
    if format_name == "teams":
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": "Fieldnote maintenance notification",
            "title": f"{payload['asset_tag']} · {payload['status']}",
            "text": "Approval or work-order status changed.",
            "potentialAction": [{
                "@type": "OpenUri",
                "name": "Open Fieldnote",
                "targets": [{"os": "default", "uri": payload["url"]}],
            }],
        }
    return {
        "text": f"Fieldnote: {payload['asset_tag']} · {payload['status']} · {payload['url']}",
    }


async def _send_webhook(payload: dict[str, Any], dedupe_key: str) -> None:
    body = build_webhook_payload(payload, settings.notification_webhook_format)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        response = await client.post(
            settings.notification_webhook_url or "",
            json=body,
            headers={"Idempotency-Key": dedupe_key},
        )
        response.raise_for_status()


async def _deliver(channel: str, payload: dict[str, Any], dedupe_key: str) -> None:
    if channel == "email":
        await asyncio.to_thread(_send_email, payload, dedupe_key)
    elif channel == "webhook":
        await _send_webhook(payload, dedupe_key)
    else:
        raise ValueError("Unsupported notification channel")


async def _record_result(item_id: str, *, error: Exception | None = None) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        async with session.begin():
            item = await session.scalar(select(NotificationOutbox).where(NotificationOutbox.id == item_id).with_for_update())
            if item is None or item.status == "sent":
                return
            item.locked_at = None
            if error is None:
                item.status = "sent"
                item.sent_at = now
                item.last_error = None
                return
            terminal = item.attempts >= MAX_ATTEMPTS
            item.status = "failed" if terminal else "retry"
            item.last_error = type(error).__name__[:100]
            item.next_attempt_at = now + timedelta(seconds=min(3600, 2 ** min(item.attempts, 11)))


async def process_one_notification() -> bool:
    claimed = await _claim_due_item()
    if claimed is None:
        return False
    item_id, channel, dedupe_key, payload, attempts = claimed
    try:
        await _deliver(channel, payload, dedupe_key)
    except Exception as error:
        logger.warning("Notification delivery attempt %s failed (%s)", attempts, type(error).__name__)
        await _record_result(item_id, error=error)
    else:
        await _record_result(item_id)
    return True


async def notification_worker_loop(poll_interval_seconds: float = 1.0) -> None:
    while True:
        try:
            processed = await process_one_notification()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification outbox worker iteration failed")
            await asyncio.sleep(min(5.0, poll_interval_seconds))
        else:
            if not processed:
                await asyncio.sleep(poll_interval_seconds)