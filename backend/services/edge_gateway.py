import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from schemas.asset import TelemetryBatch, TelemetryIngest


def normalize_gateway_reading(
    payload: dict[str, Any],
    *,
    source: str,
    require_timestamp: bool = False,
) -> TelemetryIngest:
    normalized = dict(payload)
    if require_timestamp and not normalized.get("timestamp"):
        raise ValueError("Historian readings require an explicit timezone-aware timestamp.")
    normalized["source"] = source
    if not normalized.get("source_event_id"):
        signature = json.dumps(normalized, sort_keys=True, default=str, separators=(",", ":"))
        normalized["source_event_id"] = f"{source}:{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:48]}"
    return TelemetryIngest.model_validate(normalized)


async def post_reading_batch(
    client: httpx.AsyncClient,
    api_url: str,
    readings: list[TelemetryIngest],
) -> dict[str, Any]:
    if not readings or len(readings) > 500:
        raise ValueError("A telemetry request must contain between 1 and 500 readings.")
    response = await client.post(
        f"{api_url.rstrip('/')}/telemetry/ingest/batch",
        json=TelemetryBatch(readings=readings).model_dump(mode="json"),
    )
    response.raise_for_status()
    return response.json()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)