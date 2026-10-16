import asyncio
import logging

from services.ingestion_service import process_pending_document

logger = logging.getLogger(__name__)


async def document_ingestion_worker_loop(poll_interval_seconds: float = 1.0) -> None:
    while True:
        try:
            processed = await process_pending_document()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Knowledge document worker iteration failed")
            await asyncio.sleep(min(5.0, poll_interval_seconds))
        else:
            if not processed:
                await asyncio.sleep(poll_interval_seconds)