import argparse
import asyncio
import csv
import json
import os
from pathlib import Path
from typing import Any

import httpx

from core.config import settings
from services.edge_gateway import normalize_gateway_reading, post_reading_batch


def load_historian_readings(path: Path):
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as source_file:
            records: list[dict[str, Any]] = list(csv.DictReader(source_file))
    elif path.suffix.lower() in {".jsonl", ".ndjson"}:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("JSON historian input must be an array of telemetry readings.")
        records = loaded
    else:
        raise ValueError("Historian input must be CSV, JSON, JSONL, or NDJSON.")
    readings = [normalize_gateway_reading(record, source="historian", require_timestamp=True) for record in records]
    return sorted(readings, key=lambda reading: (str(reading.asset_id), reading.timestamp))


async def backfill(path: Path, api_url: str, api_key: str | None, batch_size: int) -> int:
    readings = load_historian_readings(path)
    headers = {"X-API-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        for offset in range(0, len(readings), batch_size):
            await post_reading_batch(client, api_url, readings[offset:offset + batch_size])
    return len(readings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill timestamped historian telemetry through Fieldnote's validated ingest API.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--api-url", default=os.getenv("FIELDNOTE_API_URL", settings.fieldnote_api_url))
    parser.add_argument("--api-key", default=settings.api_key)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    accepted = asyncio.run(backfill(args.input, args.api_url, args.api_key, args.batch_size))
    print(f"Submitted {accepted} historian readings in timestamp order.")


if __name__ == "__main__":
    main()