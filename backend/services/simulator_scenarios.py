from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import Asset, SensorData, SimulatorScenario, SimulatorScenarioEvent
from schemas.asset import TelemetryIngest
from schemas.simulator import ScenarioName
from core.security import Principal
from services.telemetry_ingest import add_validated_reading

SCENARIO_RECENT_WINDOW = timedelta(minutes=30)
SIMULATOR_PRINCIPAL = Principal("simulator", frozenset({"service"}), False, "simulator")


def baseline_values(asset: Asset) -> tuple[float, float, float, float]:
    vibration_threshold = asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s
    temperature_threshold = asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c
    vibration = max(0.1, min(vibration_threshold * 0.45, 4.0))
    temperature = max(-49.0, min(temperature_threshold - max(5.0, abs(temperature_threshold) * 0.2), 70.0))
    return vibration, temperature, 3.4, 1450.0


async def latest_values(db: AsyncSession, asset_id) -> tuple[float, float, float, float] | None:
    row = await db.scalar(
        select(SensorData)
        .where(SensorData.asset_id == asset_id)
        .order_by(SensorData.timestamp.desc())
        .limit(1)
    )
    if row is None:
        return None
    return row.vibration_mm_s, row.temperature_c, row.pressure_bar, row.rpm


def build_scenario_reading(
    asset: Asset,
    scenario: ScenarioName,
    severity: float,
    index: int,
    duration_readings: int,
    previous: tuple[float, float, float, float] | None = None,
) -> TelemetryIngest | None:
    if scenario == "data_dropout":
        return None

    vibration, temperature, pressure, rpm = previous or baseline_values(asset)
    baseline = baseline_values(asset)
    vibration_threshold = asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s
    temperature_threshold = asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c
    progress = min(1.0, (index + 1) / max(1, duration_readings))

    if scenario == "vibration_rise":
        vibration = vibration_threshold * (0.72 + 0.6 * max(severity, 0.5) * progress)
        temperature = baseline[1]
    elif scenario == "overheating":
        temperature = temperature_threshold * (0.72 + 0.6 * max(severity, 0.5) * progress)
        vibration = baseline[0]
    elif scenario == "sudden_spike":
        if index == 0:
            vibration = max(vibration, vibration_threshold * (1.25 + 0.5 * severity))
            temperature = max(temperature, temperature_threshold * (1.08 + 0.2 * severity))
        else:
            vibration, temperature = baseline[:2]
    elif scenario == "sensor_flatline":
        pass
    elif scenario == "healthy_baseline":
        vibration, temperature, pressure, rpm = baseline
    elif scenario == "invalid_reading":
        vibration = 501.0

    return TelemetryIngest.model_validate({
        "asset_id": asset.id,
        "timestamp": datetime.now(timezone.utc),
        "vibration_mm_s": vibration,
        "temperature_c": temperature,
        "pressure_bar": pressure,
        "rpm": rpm,
        "source": f"scenario:{scenario}",
    })


def add_scenario_event(
    db: AsyncSession,
    asset_id,
    scenario: str,
    event_type: str,
    principal,
    details: dict[str, object] | None = None,
) -> None:
    db.add(SimulatorScenarioEvent(
        asset_id=asset_id,
        scenario=scenario,
        event_type=event_type,
        actor_type=principal.auth_method,
        actor_id=principal.subject,
        actor_roles=sorted(principal.roles),
        actor_verified=principal.verified,
        details=details or {},
    ))


async def scenario_is_active_or_recent(db: AsyncSession, asset_id, now: datetime | None = None) -> SimulatorScenario | None:
    now = now or datetime.now(timezone.utc)
    scenario = await db.get(SimulatorScenario, asset_id)
    if scenario and (scenario.active or scenario.recent_until >= now):
        return scenario
    return None


async def process_scenario_tick(db: AsyncSession, asset: Asset, now: datetime) -> bool:
    scenario = await db.get(SimulatorScenario, asset.id)
    if scenario is None or not scenario.active:
        return False
    previous = await latest_values(db, asset.id)
    index = scenario.duration_readings - scenario.remaining_readings
    try:
        reading = build_scenario_reading(
            asset,
            scenario.scenario,
            scenario.severity,
            index,
            scenario.duration_readings,
            previous,
        )
    except ValidationError as error:
        reading = None
        details: dict[str, object] = {"reason": "validated_ingest_rejected_payload", "validation_errors": len(error.errors())}
        event_type = "reading_rejected"
    else:
        details = {"reading_generated": reading is not None}
        event_type = "sampled"
        if reading is not None:
            await add_validated_reading(reading, db, asset)

    scenario.remaining_readings = max(0, scenario.remaining_readings - 1)
    scenario.recent_until = now + SCENARIO_RECENT_WINDOW
    add_scenario_event(db, asset.id, scenario.scenario, event_type, SIMULATOR_PRINCIPAL, details)
    if scenario.remaining_readings == 0:
        scenario.active = False
        add_scenario_event(db, asset.id, scenario.scenario, "completed", SIMULATOR_PRINCIPAL, {
            "generated_duration": scenario.duration_readings,
        })
    return True