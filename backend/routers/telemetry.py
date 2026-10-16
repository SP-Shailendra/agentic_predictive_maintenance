from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.database import get_db
from core.security import Principal, plant_scope_filter, require_api_key, require_roles, WORKFLOW_READ_ROLES
from models import Asset, SensorData
from schemas.asset import TelemetryBatch, TelemetryIngest
from services.telemetry_ingest import add_validated_reading, update_asset_condition

router = APIRouter(prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(require_api_key)])


def _reading(row: SensorData) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "asset_id": str(row.asset_id),
        "timestamp": row.timestamp.isoformat(),
        "vibration_mm_s": row.vibration_mm_s,
        "temperature_c": row.temperature_c,
        "pressure_bar": row.pressure_bar,
        "rpm": row.rpm,
        "source": row.source,
        "source_event_id": row.source_event_id,
    }


@router.get("/latest")
async def latest_telemetry(
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[dict[str, Any]]:
    query = select(SensorData).join(Asset, Asset.id == SensorData.asset_id)
    plant_filter = plant_scope_filter(_principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    rows = await db.scalars(query.order_by(SensorData.timestamp.desc()).limit(60))
    return [_reading(row) for row in rows.all()]


@router.get("/{asset_id}")
async def asset_telemetry(
    asset_id: str,
    limit: int = Query(default=60, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[dict[str, Any]]:
    asset_query = select(Asset.id).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(_principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    asset = await db.scalar(asset_query)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    rows = await db.scalars(
        select(SensorData).where(SensorData.asset_id == asset).order_by(SensorData.timestamp.desc()).limit(limit)
    )
    return [_reading(row) for row in reversed(rows.all())]


@router.post("/ingest", status_code=201)
async def ingest_telemetry(
    payload: TelemetryIngest,
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles("operator", "reliability_engineer", allow_service=True)),
) -> dict[str, str]:
    asset_query = select(Asset).where(Asset.id == payload.asset_id)
    plant_filter = plant_scope_filter(_principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    asset = await db.scalar(asset_query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    result = await add_validated_reading(payload, db, asset)
    if result["status"] == "duplicate":
        await db.rollback()
        return result
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if payload.source_event_id:
            existing_id = await db.scalar(
                select(SensorData.id).where(
                    SensorData.asset_id == asset.id,
                    SensorData.timestamp == timestamp,
                    SensorData.source_event_id == payload.source_event_id,
                )
            )
            if existing_id is not None:
                return {"id": str(existing_id), "status": "duplicate"}
        raise
    return result


@router.post("/ingest/batch", status_code=201)
async def ingest_telemetry_batch(
    payload: TelemetryBatch,
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles("operator", "reliability_engineer", allow_service=True)),
) -> dict[str, int | str]:
    asset_ids = {reading.asset_id for reading in payload.readings}
    asset_query = select(Asset).where(Asset.id.in_(asset_ids))
    plant_filter = plant_scope_filter(_principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    assets = await db.scalars(asset_query.order_by(Asset.id).with_for_update())
    assets_by_id = {asset.id: asset for asset in assets.all()}
    missing = asset_ids - assets_by_id.keys()
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown asset IDs: {', '.join(sorted(map(str, missing)))}")

    latest_rows = await db.execute(
        select(SensorData.asset_id, func.max(SensorData.timestamp))
        .where(SensorData.asset_id.in_(asset_ids))
        .group_by(SensorData.asset_id)
    )
    latest_by_asset = dict(latest_rows.all())
    now = datetime.now(timezone.utc)
    source_event_ids = {reading.source_event_id for reading in payload.readings if reading.source_event_id}
    existing_signatures: set[tuple[object, datetime, str]] = set()
    if source_event_ids:
        existing_rows = await db.execute(
            select(SensorData.asset_id, SensorData.timestamp, SensorData.source_event_id)
            .where(
                SensorData.asset_id.in_(asset_ids),
                SensorData.source_event_id.in_(source_event_ids),
            )
        )
        existing_signatures = {
            (asset_id, timestamp.astimezone(timezone.utc), source_event_id)
            for asset_id, timestamp, source_event_id in existing_rows.all()
            if source_event_id is not None
        }
    rows: list[SensorData] = []
    seen_signatures: set[tuple[object, datetime, str]] = set()
    duplicate_count = 0
    for reading in payload.readings:
        timestamp = reading.timestamp or now
        timestamp = timestamp.astimezone(timezone.utc)
        signature = (reading.asset_id, timestamp, reading.source_event_id) if reading.source_event_id else None
        if signature and (signature in existing_signatures or signature in seen_signatures):
            duplicate_count += 1
            continue
        if signature:
            seen_signatures.add(signature)
        row = SensorData(
            asset_id=reading.asset_id,
            timestamp=timestamp,
            vibration_mm_s=reading.vibration_mm_s,
            temperature_c=reading.temperature_c,
            pressure_bar=reading.pressure_bar,
            rpm=reading.rpm,
            source=reading.source,
            source_event_id=reading.source_event_id,
        )
        rows.append(row)

    for row in sorted(rows, key=lambda reading: reading.timestamp):
        if row.timestamp > latest_by_asset.get(row.asset_id, datetime.min.replace(tzinfo=timezone.utc)):
            update_asset_condition(assets_by_id[row.asset_id], row.vibration_mm_s, row.temperature_c, row.timestamp)
    db.add_all(rows)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate telemetry event in concurrent ingest; retry the batch.")
    return {"accepted": len(rows), "duplicates": duplicate_count, "status": "accepted"}
