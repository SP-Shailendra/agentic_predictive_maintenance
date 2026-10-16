import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import append_workflow_event
from core.config import settings
from core.database import get_db
from core.security import Principal, plant_scope_filter, require_api_key, require_roles, WORKFLOW_READ_ROLES
from models import Asset, AssetAlertSnooze, AssetThresholdChange, Plant, SensorData, SimulatorScenarioEvent, WorkOrder, WorkflowEvent
from schemas.asset import AssetAlertAcknowledgementRead, AssetAlertSnoozeRead, AssetAlertSnoozeRequest, AssetHistoryEventRead, AssetRead, AssetThresholdChangeRead, AssetThresholdUpdate
from services.telemetry_ingest import ALERT_RECOVERY_MAX_GAP_SECONDS, ALERT_RECOVERY_READINGS, acknowledge_asset_alert

router = APIRouter(prefix="/assets", tags=["assets"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[AssetRead])
async def list_assets(
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[AssetRead]:
    latest = (
        select(SensorData.asset_id.label("asset_id"), func.max(SensorData.timestamp).label("timestamp"))
        .group_by(SensorData.asset_id)
        .subquery()
    )
    query = (
        select(Asset, Plant.code, Plant.name, SensorData.vibration_mm_s, SensorData.temperature_c)
        .join(Plant, Plant.id == Asset.plant_id)
        .outerjoin(latest, latest.c.asset_id == Asset.id)
        .outerjoin(SensorData, (SensorData.asset_id == latest.c.asset_id) & (SensorData.timestamp == latest.c.timestamp))
    )
    plant_filter = plant_scope_filter(principal, Asset.plant_id, plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    rows = await db.execute(query.order_by(Asset.name))
    return [
        AssetRead.model_validate(asset).model_copy(update={
            "plant_code": plant_code,
            "plant_name": plant_name,
            "latest_vibration_mm_s": vibration,
            "latest_temperature_c": temperature,
            "vibration_threshold_mm_s": asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s,
            "temperature_threshold_c": asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c,
            "vibration_threshold_source": "asset" if asset.vibration_threshold_mm_s is not None else "demo",
            "temperature_threshold_source": "asset" if asset.temperature_threshold_c is not None else "demo",
        })
        for asset, plant_code, plant_name, vibration, temperature in rows.all()
    ]


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> AssetRead:
    query = select(Asset, Plant.code, Plant.name).join(Plant).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    row = (await db.execute(query)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset, plant_code, plant_name = row
    latest = await db.scalar(
        select(SensorData).where(SensorData.asset_id == asset.id).order_by(SensorData.timestamp.desc()).limit(1)
    )
    return AssetRead.model_validate(asset).model_copy(update={
        "plant_code": plant_code,
        "plant_name": plant_name,
        "latest_vibration_mm_s": latest.vibration_mm_s if latest else None,
        "latest_temperature_c": latest.temperature_c if latest else None,
        "vibration_threshold_mm_s": asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s,
        "temperature_threshold_c": asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c,
        "vibration_threshold_source": "asset" if asset.vibration_threshold_mm_s is not None else "demo",
        "temperature_threshold_source": "asset" if asset.temperature_threshold_c is not None else "demo",
    })


@router.post("/{asset_id}/alert/acknowledge", response_model=AssetAlertAcknowledgementRead)
async def acknowledge_alert(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("operator", "reliability_engineer")),
) -> AssetAlertAcknowledgementRead:
    query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    asset = await db.scalar(query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not asset.alert_active:
        raise HTTPException(status_code=409, detail="This asset has no open alert.")
    acknowledge_asset_alert(asset, principal.subject, datetime.now(timezone.utc))
    await db.commit()
    return AssetAlertAcknowledgementRead(
        asset_id=asset.id,
        alert_active=asset.alert_active,
        alert_acknowledged_at=asset.alert_acknowledged_at,
        alert_normal_readings=asset.alert_normal_readings,
        recovery_readings_required=ALERT_RECOVERY_READINGS,
        recovery_max_gap_seconds=ALERT_RECOVERY_MAX_GAP_SECONDS,
        status="acknowledged" if asset.alert_active else "cleared",
    )


@router.post("/{asset_id}/alert/snooze", response_model=AssetAlertSnoozeRead, status_code=201)
async def snooze_alert(
    asset_id: uuid.UUID,
    request: AssetAlertSnoozeRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("operator", "reliability_engineer")),
) -> AssetAlertSnoozeRead:
    query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    asset = await db.scalar(query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not asset.alert_active:
        raise HTTPException(status_code=409, detail="Only an open alert can be snoozed.")
    now = datetime.now(timezone.utc)
    snoozed_until = now + timedelta(minutes=request.minutes)
    subject = principal.subject if principal.verified else None
    asset.alert_snoozed_until = snoozed_until
    asset.alert_snoozed_by = subject
    db.add(AssetAlertSnooze(
        asset_id=asset.id,
        snoozed_by_sub=subject,
        snoozed_until=snoozed_until,
        reason=request.reason,
    ))
    await db.commit()
    return AssetAlertSnoozeRead(
        asset_id=asset.id,
        alert_snoozed_until=snoozed_until,
        alert_snoozed_by=subject,
        reason=request.reason,
    )


@router.patch("/{asset_id}/thresholds", response_model=AssetRead)
async def update_asset_thresholds(
    asset_id: uuid.UUID,
    update: AssetThresholdUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("reliability_engineer", "manager")),
) -> AssetRead:
    query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    asset = await db.scalar(query.with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    old_vibration = asset.vibration_threshold_mm_s
    old_temperature = asset.temperature_threshold_c
    asset.vibration_threshold_mm_s = update.vibration_threshold_mm_s
    asset.temperature_threshold_c = update.temperature_threshold_c
    db.add(AssetThresholdChange(
        asset_id=asset.id,
        changed_by_sub=principal.subject if principal.verified else None,
        vibration_old_mm_s=old_vibration,
        vibration_new_mm_s=update.vibration_threshold_mm_s,
        temperature_old_c=old_temperature,
        temperature_new_c=update.temperature_threshold_c,
        reason=update.reason,
    ))
    await db.commit()
    await db.refresh(asset)
    return AssetRead.model_validate(asset).model_copy(update={
        "vibration_threshold_source": "asset" if asset.vibration_threshold_mm_s is not None else "demo",
        "temperature_threshold_source": "asset" if asset.temperature_threshold_c is not None else "demo",
    })


@router.get("/{asset_id}/threshold-history", response_model=list[AssetThresholdChangeRead])
async def asset_threshold_history(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("reliability_engineer", "manager")),
) -> list[AssetThresholdChange]:
    asset_query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    if await db.scalar(asset_query) is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    rows = await db.scalars(
        select(AssetThresholdChange)
        .where(AssetThresholdChange.asset_id == asset_id)
        .order_by(AssetThresholdChange.created_at.desc())
        .limit(100)
    )
    return list(rows.all())


@router.get("/{asset_id}/history", response_model=list[AssetHistoryEventRead])
async def asset_history(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[AssetHistoryEventRead]:
    asset_query = select(Asset).where(Asset.id == asset_id)
    plant_filter = plant_scope_filter(principal, Asset.plant_id)
    if plant_filter is not None:
        asset_query = asset_query.where(plant_filter)
    asset = await db.scalar(asset_query)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    timeline: list[AssetHistoryEventRead] = []
    readings = list((await db.scalars(
        select(SensorData).where(SensorData.asset_id == asset_id)
        .order_by(SensorData.timestamp.desc()).limit(5000)
    )).all())
    readings.reverse()
    threshold_changes = list((await db.scalars(
        select(AssetThresholdChange).where(AssetThresholdChange.asset_id == asset_id)
        .order_by(AssetThresholdChange.created_at).limit(1000)
    )).all())
    vibration_limit = (
        threshold_changes[0].vibration_old_mm_s if threshold_changes and threshold_changes[0].vibration_old_mm_s is not None
        else settings.default_vibration_threshold_mm_s
    )
    temperature_limit = (
        threshold_changes[0].temperature_old_c if threshold_changes and threshold_changes[0].temperature_old_c is not None
        else settings.default_temperature_threshold_c
    )
    change_index = 0
    previous_status = None
    for reading in readings:
        while change_index < len(threshold_changes) and threshold_changes[change_index].created_at <= reading.timestamp:
            change = threshold_changes[change_index]
            vibration_limit = change.vibration_new_mm_s if change.vibration_new_mm_s is not None else settings.default_vibration_threshold_mm_s
            temperature_limit = change.temperature_new_c if change.temperature_new_c is not None else settings.default_temperature_threshold_c
            change_index += 1
        status = "attention" if reading.vibration_mm_s >= vibration_limit or reading.temperature_c >= temperature_limit else "operational"
        if status != previous_status:
            timeline.append(AssetHistoryEventRead(
                created_at=reading.timestamp,
                event_type="status_change" if previous_status is not None else "status_observed",
                title=f"Condition {'changed to ' if previous_status is not None else 'observed as '}{status.replace('_', ' ').title()}",
                detail=f"Estimated from stored telemetry ({reading.source}); historical threshold settings may be incomplete.",
                actor="telemetry-derived",
            ))
            previous_status = status

    event_rows = await db.execute(
        select(WorkflowEvent, WorkOrder.summary)
        .join(WorkOrder, WorkOrder.id == WorkflowEvent.work_order_id)
        .where(WorkOrder.asset_id == asset_id)
        .order_by(WorkflowEvent.created_at.desc()).limit(1000)
    )
    for event, summary in event_rows.all():
        safe_detail = None
        if event.event_type == "approval_decision":
            safe_detail = f"Decision: {event.details.get('decision', 'recorded')}"
        elif event.event_type == "lifecycle_status_changed":
            safe_detail = f"Status: {event.details.get('status', event.to_status or 'updated')}"
        elif event.event_type == "technician_assigned":
            safe_detail = "Technician assignment updated."
        elif event.event_type == "approval_overdue":
            safe_detail = "Approval SLA exceeded."
        timeline.append(AssetHistoryEventRead(
            work_order_id=event.work_order_id,
            created_at=event.created_at,
            event_type=event.event_type,
            title=event.event_type.replace("_", " ").title(),
            detail=safe_detail or summary,
            actor=event.actor_id or event.actor_type,
        ))

    changes = await db.scalars(
        select(AssetThresholdChange).where(AssetThresholdChange.asset_id == asset_id)
        .order_by(AssetThresholdChange.created_at.desc()).limit(1000)
    )
    for change in changes.all():
        timeline.append(AssetHistoryEventRead(
            created_at=change.created_at,
            event_type="threshold_changed",
            title="Advisory limits changed",
            detail=change.reason,
            actor=change.changed_by_sub,
        ))

    scenarios = await db.scalars(
        select(SimulatorScenarioEvent).where(SimulatorScenarioEvent.asset_id == asset_id)
        .order_by(SimulatorScenarioEvent.created_at.desc()).limit(500)
    )
    for event in scenarios.all():
        timeline.append(AssetHistoryEventRead(
            created_at=event.created_at,
            event_type=f"scenario_{event.event_type}",
            title=f"Demo scenario {event.event_type}: {event.scenario.replace('_', ' ')}",
            detail="Synthetic scenario event.",
            actor=event.actor_id or event.actor_type,
        ))

    snoozes = await db.scalars(
        select(AssetAlertSnooze).where(AssetAlertSnooze.asset_id == asset_id)
        .order_by(AssetAlertSnooze.created_at.desc()).limit(100)
    )
    for event in snoozes.all():
        timeline.append(AssetHistoryEventRead(
            created_at=event.created_at,
            event_type="alert_snoozed",
            title="Condition alert snoozed",
            detail=f"{event.reason} · until {event.snoozed_until.isoformat()}",
            actor=event.snoozed_by_sub,
        ))

    return sorted(timeline, key=lambda event: event.created_at, reverse=True)[:250]
