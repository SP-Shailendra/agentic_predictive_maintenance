from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.security import Principal, plant_scope_filter, require_api_key, require_roles, WORKFLOW_READ_ROLES
from models import Asset, SimulatorScenario
from schemas.simulator import ScenarioRead, ScenarioStart
from services.simulator_scenarios import (
    SCENARIO_RECENT_WINDOW,
    add_scenario_event,
    build_scenario_reading,
    latest_values,
)
from services.telemetry_ingest import add_validated_reading

router = APIRouter(prefix="/simulator", tags=["simulator"], dependencies=[Depends(require_api_key)])


def _require_simulator() -> None:
    if not settings.simulator_enabled:
        raise HTTPException(status_code=403, detail="Simulation scenarios are disabled.")


@router.get("/scenarios", response_model=list[ScenarioRead])
async def list_scenarios(
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[SimulatorScenario]:
    _require_simulator()
    query = (
        select(SimulatorScenario).join(Asset, Asset.id == SimulatorScenario.asset_id)
        .where(SimulatorScenario.active.is_(True))
        .order_by(SimulatorScenario.started_at.desc())
    )
    plant_filter = plant_scope_filter(_principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    rows = await db.scalars(query)
    return list(rows.all())


@router.post("/scenarios")
async def start_scenario(
    payload: ScenarioStart,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("operator", "reliability_engineer")),
) -> dict[str, object]:
    _require_simulator()
    asset_query = select(Asset).where(Asset.id == payload.asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    asset = await db.scalar(asset_query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    previous = await latest_values(db, asset.id)
    try:
        first_reading = build_scenario_reading(asset, payload.scenario, payload.severity, 0, payload.duration_readings, previous)
    except ValidationError as error:
        add_scenario_event(db, asset.id, payload.scenario, "start_rejected", principal, {
            "duration_readings": payload.duration_readings,
            "severity": payload.severity,
            "reason": "validated_ingest_rejected_payload",
            "validation_errors": len(error.errors()),
        })
        await db.commit()
        return {
            "asset_id": str(asset.id),
            "scenario": payload.scenario,
            "active": False,
            "reading_rejected": True,
            "message": "The invalid sample was rejected by telemetry validation and was not stored.",
        }

    now = datetime.now(timezone.utc)
    scenario = await db.get(SimulatorScenario, asset.id)
    if scenario is None:
        scenario = SimulatorScenario(
            asset_id=asset.id,
            scenario=payload.scenario,
            severity=payload.severity,
            duration_readings=payload.duration_readings,
            remaining_readings=payload.duration_readings,
            active=True,
            force_llm_fallback=payload.force_llm_fallback,
            started_by_sub=principal.subject,
            recent_until=now + SCENARIO_RECENT_WINDOW,
        )
        db.add(scenario)
    else:
        if scenario.active:
            add_scenario_event(db, asset.id, scenario.scenario, "stopped", principal, {"reason": "replaced_by_new_scenario"})
        scenario.scenario = payload.scenario
        scenario.severity = payload.severity
        scenario.duration_readings = payload.duration_readings
        scenario.remaining_readings = payload.duration_readings
        scenario.active = True
        scenario.force_llm_fallback = payload.force_llm_fallback
        scenario.started_by_sub = principal.subject
        scenario.started_at = now
        scenario.recent_until = now + SCENARIO_RECENT_WINDOW

    add_scenario_event(db, asset.id, payload.scenario, "started", principal, {
        "duration_readings": payload.duration_readings,
        "severity": payload.severity,
        "force_llm_fallback": payload.force_llm_fallback,
    })
    reading_result = None
    if first_reading is not None:
        reading_result = await add_validated_reading(first_reading, db, asset)
        scenario.remaining_readings = max(0, payload.duration_readings - 1)

    if scenario.remaining_readings == 0:
        scenario.active = False
        add_scenario_event(db, asset.id, payload.scenario, "completed", principal, {"generated_readings": 1 if first_reading else 0})
    await db.commit()
    return {
        "asset_id": str(asset.id),
        "scenario": scenario.scenario,
        "active": scenario.active,
        "remaining_readings": scenario.remaining_readings,
        "force_llm_fallback": scenario.force_llm_fallback,
        "reading": reading_result,
        "message": "Scenario samples are advisory only. Click Run diagnosis to start the agent workflow.",
    }


@router.delete("/scenarios/{asset_id}")
async def stop_scenario(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("operator", "reliability_engineer")),
) -> dict[str, object]:
    _require_simulator()
    asset_query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    asset = await db.scalar(asset_query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    scenario = await db.get(SimulatorScenario, asset.id)
    previous = scenario.scenario if scenario else "healthy_baseline"
    if scenario:
        scenario.active = False
        scenario.remaining_readings = 0
        scenario.force_llm_fallback = False
        scenario.recent_until = datetime.now(timezone.utc) + SCENARIO_RECENT_WINDOW
    reading = build_scenario_reading(asset, "healthy_baseline", 0, 0, 1)
    result = await add_validated_reading(reading, db, asset)
    add_scenario_event(db, asset.id, previous, "stopped", principal, {"baseline_reading_id": result["id"]})
    await db.commit()
    return {"asset_id": str(asset.id), "active": False, "reading": result, "message": "Scenario stopped; a healthy baseline sample was ingested."}