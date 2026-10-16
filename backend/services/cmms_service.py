import uuid
from urllib.parse import quote

import httpx

from core.config import settings


async def dispatch_work_order(payload: dict[str, object]) -> str:
    if payload.get("simulated_only") is True:
        return f"SIM-{uuid.uuid4().hex[:10].upper()}"
    if not settings.cmms_api_url:
        return f"SIM-{uuid.uuid4().hex[:10].upper()}"
    work_order_id = str(payload.get("work_order_id", ""))
    if not work_order_id:
        raise ValueError("A work_order_id is required for idempotent CMMS dispatch")
    headers = {"Idempotency-Key": work_order_id}
    if settings.cmms_api_token:
        headers["Authorization"] = f"Bearer {settings.cmms_api_token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        response = await client.post(settings.cmms_api_url, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json() if response.content else {}
    if not isinstance(result, dict):
        raise ValueError("CMMS response must be a JSON object")
    reference = result.get("id") or result.get("work_order_id")
    if not reference:
        raise ValueError("CMMS response must include an id or work_order_id")
    return str(reference)


def map_cmms_status(external_status: str) -> str | None:
    normalized = external_status.strip().casefold()
    return next(
        (target for source, target in settings.cmms_status_map.items() if source.strip().casefold() == normalized),
        None,
    )


async def fetch_work_order_status(reference: str) -> str:
    if not settings.cmms_status_url:
        raise RuntimeError("CMMS status synchronization is not configured.")
    url = settings.cmms_status_url.format(reference=quote(reference, safe=""))
    headers = {"Authorization": f"Bearer {settings.cmms_api_token}"} if settings.cmms_api_token else {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        raise ValueError("CMMS status response must be a JSON object containing a string status.")
    return result["status"].strip()[:80]
