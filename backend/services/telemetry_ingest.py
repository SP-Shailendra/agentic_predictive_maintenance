from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import Asset, SensorData
from schemas.asset import TelemetryIngest


ALERT_RECOVERY_READINGS = 3
ALERT_RECOVERY_MAX_GAP_SECONDS = 60


def update_asset_condition(asset: Asset, vibration: float, temperature: float, timestamp: datetime) -> None:
    previous_timestamp = asset.alert_last_reading_at
    if previous_timestamp is not None and timestamp <= previous_timestamp:
        return
    vibration_limit = asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s
    temperature_limit = asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c
    breached = vibration >= vibration_limit or temperature >= temperature_limit
    asset.status = "attention" if breached else "operational"
    asset.alert_last_reading_at = timestamp
    if breached:
        if not asset.alert_active:
            asset.alert_active = True
            asset.alert_started_at = timestamp
            asset.alert_acknowledged_at = None
            asset.alert_acknowledged_by = None
        asset.alert_normal_readings = 0
    elif asset.alert_active:
        if previous_timestamp is None or (timestamp - previous_timestamp).total_seconds() > ALERT_RECOVERY_MAX_GAP_SECONDS:
            asset.alert_normal_readings = 0
        asset.alert_normal_readings = min(ALERT_RECOVERY_READINGS, (asset.alert_normal_readings or 0) + 1)
        if asset.alert_acknowledged_at is not None and asset.alert_normal_readings >= ALERT_RECOVERY_READINGS:
            asset.alert_active = False


def acknowledge_asset_alert(asset: Asset, subject: str, now: datetime) -> None:
    if asset.alert_acknowledged_at is None:
        asset.alert_acknowledged_at = now
        asset.alert_acknowledged_by = subject
    last_reading = asset.alert_last_reading_at
    if (
        asset.status == "operational"
        and asset.alert_normal_readings >= ALERT_RECOVERY_READINGS
        and last_reading is not None
        and 0 <= (now - last_reading).total_seconds() <= ALERT_RECOVERY_MAX_GAP_SECONDS
    ):
        asset.alert_active = False


async def add_validated_reading(payload: TelemetryIngest, db: AsyncSession, asset: Asset) -> dict[str, str]:
    timestamp = payload.timestamp or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
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

    latest_timestamp = await db.scalar(
        select(SensorData.timestamp)
        .where(SensorData.asset_id == asset.id)
        .order_by(SensorData.timestamp.desc())
        .limit(1)
    )
    row = SensorData(
        asset_id=asset.id,
        timestamp=timestamp,
        vibration_mm_s=payload.vibration_mm_s,
        temperature_c=payload.temperature_c,
        pressure_bar=payload.pressure_bar,
        rpm=payload.rpm,
        source=payload.source,
        source_event_id=payload.source_event_id,
    )
    if latest_timestamp is None or timestamp >= latest_timestamp:
        update_asset_condition(asset, payload.vibration_mm_s, payload.temperature_c, timestamp)
    db.add(row)
    await db.flush()
    return {"id": str(row.id), "status": "accepted"}