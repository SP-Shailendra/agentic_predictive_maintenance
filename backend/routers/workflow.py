import uuid
from datetime import datetime, timezone
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.state import WorkflowState
from agents.workflow_graph import workflow_graph
from core.config import settings
from core.database import get_db
from core.audit import append_workflow_event
from core.security import Principal, reject_self_approval, require_api_key, require_roles, WORKFLOW_READ_ROLES
from models import Asset, CMMSOutbox, SensorData, WorkOrder, WorkflowEvent
from services.simulator_scenarios import scenario_is_active_or_recent
from services.workflow_notifications import enqueue_workflow_notification
from schemas.workflow import (
    WorkflowAssignment,
    WorkflowCloseOut,
    WorkflowDecision,
    WorkflowEventRead,
    WorkflowLifecycleUpdate,
    WorkflowMetricsRead,
    WorkflowRead,
    WorkflowStart,
)

router = APIRouter(prefix="/workflows", tags=["workflows"], dependencies=[Depends(require_api_key)])
OPEN_INVESTIGATION_STATUSES = (
    "processing",
    "awaiting_approval",
    "dispatch_queued",
    "dispatching",
    "dispatch_retry",
    "dispatched",
    "in_progress",
)


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[WorkOrder]:
    query = select(WorkOrder)
    if plant_id is not None:
        query = query.join(Asset, Asset.id == WorkOrder.asset_id).where(Asset.plant_id == plant_id)
    rows = await db.scalars(query.order_by(WorkOrder.created_at.desc()).limit(100))
    return list(rows.all())


async def _approval_freshness(db: AsyncSession, work_order: WorkOrder) -> dict[str, Any]:
    asset = await db.get(Asset, work_order.asset_id)
    latest = await db.scalar(
        select(SensorData)
        .where(SensorData.asset_id == work_order.asset_id)
        .order_by(SensorData.timestamp.desc(), SensorData.id.desc())
        .limit(1)
    )
    state = work_order.workflow_state or {}
    raw_snapshot_at = state.get("diagnosis_snapshot_at")
    try:
        snapshot_at = datetime.fromisoformat(raw_snapshot_at) if raw_snapshot_at else work_order.created_at
    except (TypeError, ValueError):
        snapshot_at = work_order.created_at
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (datetime.now(timezone.utc) - snapshot_at).total_seconds())
    snapshot = state.get("telemetry") if isinstance(state.get("telemetry"), dict) else {}
    delta: dict[str, float | None] = {}
    readings_changed = False
    if latest is not None and latest.timestamp > snapshot_at:
        for key in ("vibration_mm_s", "temperature_c"):
            original = snapshot.get(key)
            current = getattr(latest, key)
            delta[key] = float(current - original) if original is not None else None
            readings_changed |= original is not None and float(original) != float(current)
    else:
        delta = {key: None for key in ("vibration_mm_s", "temperature_c")}
    reasons: list[str] = []
    if age_seconds >= settings.approval_stale_after_minutes * 60:
        reasons.append("diagnosis_age_exceeded")
    if state.get("asset_status_at_diagnosis") == "attention" and asset and asset.status == "operational":
        reasons.append("asset_returned_to_normal")
    if readings_changed:
        reasons.append("telemetry_changed")
    return {
        "diagnosis_age_seconds": age_seconds,
        "current_asset_status": asset.status if asset else None,
        "telemetry_delta": delta,
        "approval_is_stale": bool(reasons),
        "approval_stale_reasons": reasons,
    }


@router.get("/my-work", response_model=list[WorkflowRead])
async def my_work(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("technician")),
) -> list[WorkOrder]:
    rows = await db.scalars(
        select(WorkOrder)
        .where(
            WorkOrder.assigned_to_sub == principal.subject,
            WorkOrder.status.in_(("processing", "awaiting_approval", "dispatch_queued", "dispatching", "dispatch_retry", "dispatched", "in_progress", "completed")),
        )
        .order_by(WorkOrder.updated_at.desc())
        .limit(200)
    )
    return list(rows.all())


@router.get("/{work_order_id}", response_model=WorkflowRead)
async def get_workflow(
    work_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> WorkflowRead:
    work_order = await db.get(WorkOrder, work_order_id)
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = WorkflowRead.model_validate(work_order)
    return result.model_copy(update=await _approval_freshness(db, work_order))


@router.patch("/{work_order_id}/assignment", response_model=WorkflowRead)
async def assign_work_order(
    work_order_id: uuid.UUID,
    assignment: WorkflowAssignment,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("manager", "reliability_engineer")),
) -> WorkflowRead:
    work_order = await db.scalar(select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update())
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if work_order.status not in ("processing", "awaiting_approval", "dispatch_queued", "dispatching", "dispatch_retry", "dispatched", "in_progress"):
        raise HTTPException(status_code=409, detail="Only active work orders can be assigned.")
    previous_assignee = work_order.assigned_to_sub
    work_order.assigned_to_sub = assignment.assigned_to_sub.strip()
    await append_workflow_event(
        db,
        work_order,
        "technician_assigned",
        work_order.status,
        {"previous_assignee": previous_assignee, "assigned_to_sub": work_order.assigned_to_sub},
        principal,
    )
    await db.commit()
    await db.refresh(work_order)
    return WorkflowRead.model_validate(work_order).model_copy(update=await _approval_freshness(db, work_order))


@router.post("", response_model=WorkflowRead, status_code=201)
async def start_workflow(
    payload: WorkflowStart,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("operator", "reliability_engineer")),
) -> WorkOrder:
    asset = await db.scalar(select(Asset).where(Asset.id == payload.asset_id).with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    existing = await db.scalar(
        select(WorkOrder)
        .where(WorkOrder.asset_id == asset.id, WorkOrder.status.in_(OPEN_INVESTIGATION_STATUSES))
        .order_by(WorkOrder.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"An investigation is already open for {asset.asset_tag}: {existing.id}.",
        )
    scenario = await scenario_is_active_or_recent(db, asset.id)

    now = datetime.now(timezone.utc)
    work_order = WorkOrder(
        asset_id=asset.id,
        status="processing",
        summary=f"{payload.alert_type}: {asset.name}",
        workflow_state={},
        created_by_sub=principal.subject if principal.verified else None,
    )
    db.add(work_order)
    await db.flush()
    await append_workflow_event(
        db,
        work_order,
        "workflow_started",
        None,
        {"asset_id": str(asset.id), "alert_type": payload.alert_type, "severity": payload.severity},
        principal,
    )
    state: WorkflowState = {
        "alert_id": str(work_order.id),
        "asset_id": str(asset.id),
        "asset_name": asset.name,
        "asset_tag": asset.asset_tag,
        "asset_type": asset.asset_type,
        "location": asset.location,
        "plant_id": str(asset.plant_id),
        "knowledge_roles": sorted(principal.roles),
        "alert_type": payload.alert_type,
        "severity": payload.severity,
        "message": payload.message,
        "telemetry": payload.telemetry,
        "diagnosis_snapshot_at": now.isoformat(),
        "asset_status_at_diagnosis": asset.status,
        "vibration_threshold_mm_s": asset.vibration_threshold_mm_s if asset.vibration_threshold_mm_s is not None else settings.default_vibration_threshold_mm_s,
        "temperature_threshold_c": asset.temperature_threshold_c if asset.temperature_threshold_c is not None else settings.default_temperature_threshold_c,
        "vibration_threshold_source": "asset" if asset.vibration_threshold_mm_s is not None else "demo",
        "temperature_threshold_source": "asset" if asset.temperature_threshold_c is not None else "demo",
        "force_llm_fallback": bool(scenario and scenario.force_llm_fallback),
        "simulation_scenario": scenario.scenario if scenario else None,
        "simulated_only": scenario is not None,
        "status": "processing",
        "approved": False,
        "activity": [{"agent": "Edge", "message": f"Alert received from operator at {now.isoformat()}.", "status": "complete"}],
    }
    result = await workflow_graph.ainvoke(state)
    work_order.workflow_state = result
    work_order.status = str(result.get("status", "processing"))
    work_order.diagnosis = result.get("diagnosis")
    work_order.recommendation = result.get("recommendation")
    work_order.cmms_reference = result.get("cmms_reference")
    await append_workflow_event(
        db,
        work_order,
        "assessment_completed",
        "processing",
        {
            "diagnosis_confidence": result.get("diagnosis_confidence"),
            "compliance_check": result.get("compliance_check"),
            "evidence_status": result.get("evidence_status", "unavailable"),
            "evidence_error_code": result.get("evidence_error_code"),
            "evidence_sources": [
                {
                    "chunk_id": source.get("chunk_id"),
                    "document_id": source.get("document_id"),
                    "revision": source.get("revision"),
                    "page_number": source.get("page_number"),
                    "section_title": source.get("section_title"),
                }
                for source in result.get("evidence_sources", [])
            ],
        },
        principal,
    )
    if work_order.status == "awaiting_approval":
        await enqueue_workflow_notification(db, work_order, "new_awaiting_approval", asset.asset_tag)
    await db.commit()
    await db.refresh(work_order)
    return work_order


@router.post("/{work_order_id}/approve", response_model=WorkflowRead)
async def approve_workflow(
    work_order_id: uuid.UUID,
    decision: WorkflowDecision,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("approver")),
) -> WorkOrder:
    work_order = await db.scalar(
        select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
    )
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if work_order.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"Workflow cannot be approved from status '{work_order.status}'.")
    reject_self_approval(work_order.created_by_sub, principal)
    freshness = await _approval_freshness(db, work_order)
    if freshness["approval_is_stale"] and not decision.acknowledge_stale:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Approval requires acknowledgement of stale diagnosis context.",
                "requires_acknowledgement": True,
                "stale_reasons": freshness["approval_stale_reasons"],
            },
        )

    previous_status = work_order.status
    state: WorkflowState = dict(work_order.workflow_state)
    state["approved"] = True
    state["approval_comment"] = decision.comment or "Approved by operator."
    state["activity"] = [
        *state.get("activity", []),
        {"agent": "Human", "message": state["approval_comment"], "status": "approved"},
    ]
    result = await workflow_graph.ainvoke(state)
    work_order.workflow_state = result
    work_order.status = str(result.get("status", "dispatched"))
    work_order.diagnosis = result.get("diagnosis")
    work_order.recommendation = result.get("recommendation")
    work_order.cmms_reference = result.get("cmms_reference")
    work_order.approved_by_sub = principal.subject if principal.verified else None
    work_order.approved_at = datetime.now(timezone.utc)
    if result.get("status") == "dispatch_queued":
        payload: dict[str, object] = {
            "work_order_id": str(work_order.id),
            "asset_id": str(work_order.asset_id),
            "asset_tag": state.get("asset_tag"),
            "summary": f"{state.get('alert_type', 'Condition alert')} - {state.get('asset_name', 'asset')}",
            "diagnosis": result.get("diagnosis"),
            "recommendation": result.get("recommendation"),
            "severity": state.get("severity"),
            "approval_comment": state.get("approval_comment", ""),
            "simulated_only": bool(state.get("simulated_only")),
        }
        db.add(CMMSOutbox(
            work_order_id=work_order.id,
            idempotency_key=str(work_order.id),
            payload=payload,
            status="queued",
            attempts=0,
        ))
    await append_workflow_event(
        db,
        work_order,
        "approval_decision",
        previous_status,
        {
            "decision": "approved",
            "comment": state["approval_comment"],
            "stale_acknowledged": bool(decision.acknowledge_stale),
            "stale_reasons": freshness["approval_stale_reasons"],
        },
        principal,
    )
    if result.get("status") == "dispatch_queued":
        await append_workflow_event(
            db,
            work_order,
            "cmms_dispatch_queued",
            previous_status,
            {"idempotency_key": str(work_order.id)},
            principal,
        )
    await db.commit()
    await db.refresh(work_order)
    return work_order


@router.post("/{work_order_id}/lifecycle", response_model=WorkflowRead)
async def advance_work_order_lifecycle(
    work_order_id: uuid.UUID,
    update: WorkflowLifecycleUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("technician", "operator", "reliability_engineer")),
) -> WorkOrder:
    work_order = await db.scalar(
        select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
    )
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    allowed = {
        "dispatched": "in_progress",
        "in_progress": "completed",
    }
    if allowed.get(work_order.status) != update.status:
        raise HTTPException(
            status_code=409,
            detail=f"Lifecycle cannot advance from '{work_order.status}' to '{update.status}'.",
        )

    previous_status = work_order.status
    work_order.status = update.status
    state = dict(work_order.workflow_state)
    state["status"] = update.status
    state["activity"] = [
        *state.get("activity", []),
        {"agent": "Technician", "message": f"Work order marked {update.status.replace('_', ' ')}.", "status": "complete"},
    ]
    work_order.workflow_state = state
    now = datetime.now(timezone.utc)
    if update.status == "in_progress":
        work_order.in_progress_at = now
    else:
        work_order.completed_at = now
    await append_workflow_event(
        db,
        work_order,
        "lifecycle_status_changed",
        previous_status,
        {"status": update.status},
        principal,
    )
    await db.commit()
    await db.refresh(work_order)
    return work_order


@router.post("/{work_order_id}/close-out", response_model=WorkflowRead)
async def close_out_work_order(
    work_order_id: uuid.UUID,
    closeout: WorkflowCloseOut,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("technician", "operator", "reliability_engineer")),
) -> WorkOrder:
    work_order = await db.scalar(
        select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
    )
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if work_order.status != "completed":
        raise HTTPException(status_code=409, detail="Close-out is only available after a work order is completed.")

    previous_status = work_order.status
    now = datetime.now(timezone.utc)
    work_order.status = "closed"
    work_order.confirmed_root_cause = closeout.confirmed_root_cause
    work_order.alert_was_useful = closeout.alert_was_useful
    work_order.closed_by_sub = principal.subject if principal.verified else None
    work_order.closed_at = now
    state = dict(work_order.workflow_state)
    state["status"] = "closed"
    state["confirmed_root_cause"] = closeout.confirmed_root_cause
    state["alert_was_useful"] = closeout.alert_was_useful
    state["closeout_comment"] = closeout.comment or ""
    state["activity"] = [
        *state.get("activity", []),
        {"agent": "Technician", "message": "Close-out recorded with confirmed root cause and alert usefulness.", "status": "complete"},
    ]
    work_order.workflow_state = state
    await append_workflow_event(
        db,
        work_order,
        "work_order_closed",
        previous_status,
        {
            "confirmed_root_cause": closeout.confirmed_root_cause,
            "alert_was_useful": closeout.alert_was_useful,
            "comment": closeout.comment,
        },
        principal,
    )
    await db.commit()
    await db.refresh(work_order)
    return work_order


@router.post("/{work_order_id}/reject", response_model=WorkflowRead)
async def reject_workflow(
    work_order_id: uuid.UUID,
    decision: WorkflowDecision,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("approver")),
) -> WorkOrder:
    work_order = await db.scalar(
        select(WorkOrder).where(WorkOrder.id == work_order_id).with_for_update()
    )
    if work_order is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if work_order.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"Workflow cannot be rejected from status '{work_order.status}'.")
    previous_status = work_order.status
    state: dict[str, Any] = dict(work_order.workflow_state)
    state["status"] = "rejected"
    state["approval_comment"] = decision.comment or "Rejected by operator."
    state["activity"] = [
        *state.get("activity", []),
        {"agent": "Human", "message": state["approval_comment"], "status": "rejected"},
        {"agent": "Document", "message": "Rejection recorded; no CMMS dispatch was made.", "status": "complete"},
    ]
    work_order.workflow_state = state
    work_order.status = "rejected"
    work_order.rejected_by_sub = principal.subject if principal.verified else None
    await append_workflow_event(
        db,
        work_order,
        "approval_decision",
        previous_status,
        {"decision": "rejected", "comment": state["approval_comment"]},
        principal,
    )
    await db.commit()
    await db.refresh(work_order)
    return work_order


@router.get("/{work_order_id}/events", response_model=list[WorkflowEventRead])
async def workflow_events(
    work_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[WorkflowEvent]:
    if await db.get(WorkOrder, work_order_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    rows = await db.scalars(
        select(WorkflowEvent)
        .where(WorkflowEvent.work_order_id == work_order_id)
        .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
    )
    return list(rows.all())


@router.get("/metrics/maintenance", response_model=WorkflowMetricsRead)
async def maintenance_metrics(
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles("manager")),
) -> WorkflowMetricsRead:
    scenario_simulated = WorkOrder.workflow_state["simulated_only"].as_boolean()
    simulated_order = or_(scenario_simulated.is_(True), WorkOrder.cmms_reference.like("SIM-%"))
    pilot_order = and_(
        or_(scenario_simulated.is_(False), scenario_simulated.is_(None)),
        or_(WorkOrder.cmms_reference.is_(None), not_(WorkOrder.cmms_reference.like("SIM-%"))),
    )
    usefulness_query = select(WorkOrder.alert_was_useful).where(
        WorkOrder.status == "closed",
        WorkOrder.alert_was_useful.is_not(None),
        pilot_order,
    )
    root_cause_query = select(WorkOrder.confirmed_root_cause, WorkOrder.workflow_state).where(
        WorkOrder.status == "closed",
        WorkOrder.confirmed_root_cause.is_not(None),
        pilot_order,
    )
    dispatch_query = select(WorkOrder.approved_at, WorkOrder.dispatched_at).where(
        WorkOrder.approved_at.is_not(None), WorkOrder.dispatched_at.is_not(None), pilot_order,
    )
    simulated_dispatch_query = select(func.count()).select_from(WorkOrder).where(
        WorkOrder.approved_at.is_not(None), WorkOrder.dispatched_at.is_not(None), simulated_order,
    )
    status_query = select(WorkOrder.status, func.count()).where(pilot_order).group_by(WorkOrder.status)
    closed_count_query = select(func.count()).select_from(WorkOrder).where(WorkOrder.status == "closed", pilot_order)
    if plant_id is not None:
        def scope_to_plant(query):
            return query.join(Asset, Asset.id == WorkOrder.asset_id).where(Asset.plant_id == plant_id)

        usefulness_query = scope_to_plant(usefulness_query)
        root_cause_query = scope_to_plant(root_cause_query)
        dispatch_query = scope_to_plant(dispatch_query)
        simulated_dispatch_query = scope_to_plant(simulated_dispatch_query)
        status_query = scope_to_plant(status_query)
        closed_count_query = scope_to_plant(closed_count_query)
    usefulness_rows = list((await db.scalars(usefulness_query.limit(20000))).all())
    root_cause_rows = (await db.execute(root_cause_query.limit(20000))).all()
    dispatch_rows = (await db.execute(dispatch_query.limit(20000))).all()
    simulated_dispatch_count = int(await db.scalar(simulated_dispatch_query) or 0)
    status_counts = {status_name: int(count) for status_name, count in (await db.execute(status_query)).all()}
    closed_count = int(await db.scalar(closed_count_query) or 0)

    useful_count = sum(value is True for value in usefulness_rows)
    useful_rate = useful_count / len(usefulness_rows) if usefulness_rows else None
    correct_top_three = 0
    evaluated_root_causes = 0
    for confirmed, workflow_state in root_cause_rows:
        predicted = workflow_state.get("failure_modes", []) if isinstance(workflow_state, dict) else []
        if predicted:
            evaluated_root_causes += 1
            correct_top_three += confirmed in predicted[:3]
    top_three_accuracy = correct_top_three / evaluated_root_causes if evaluated_root_causes else None

    durations = sorted((dispatched - approved).total_seconds() for approved, dispatched in dispatch_rows)
    p95 = durations[max(0, ceil(.95 * len(durations)) - 1)] if durations else None
    return WorkflowMetricsRead(
        useful_alert_rate=useful_rate,
        useful_alert_count=useful_count,
        usefulness_sample_count=len(usefulness_rows),
        closed_work_order_count=closed_count,
        top_three_root_cause_accuracy=top_three_accuracy,
        root_cause_sample_count=evaluated_root_causes,
        approval_to_dispatch_p95_seconds=p95,
        dispatch_sample_count=len(durations),
        simulated_dispatch_count=simulated_dispatch_count,
        targets={"useful_alert_rate": 0.70, "top_three_root_cause_accuracy": 0.80, "approval_to_dispatch_p95_seconds": 300.0},
        status_counts=status_counts,
    )
