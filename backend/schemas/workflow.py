import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.failure_modes import FAILURE_MODE_VALUES, FailureMode


class WorkflowStart(BaseModel):
    asset_id: uuid.UUID
    alert_type: str = Field(min_length=2, max_length=100)
    severity: Literal["low", "medium", "high", "critical"] = "high"
    message: str = Field(min_length=3, max_length=1000)
    telemetry: dict[str, float] = Field(default_factory=dict)


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    status: str
    summary: str
    diagnosis: str | None
    recommendation: str | None
    workflow_state: dict[str, Any]
    cmms_reference: str | None
    assigned_to_sub: str | None = None
    diagnosis_age_seconds: float | None = None
    current_asset_status: str | None = None
    telemetry_delta: dict[str, float | None] | None = None
    approval_is_stale: bool = False
    approval_stale_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkflowDecision(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)
    acknowledge_stale: bool = False


class WorkflowAssignment(BaseModel):
    assigned_to_sub: str = Field(min_length=1, max_length=255)


class WorkflowLifecycleUpdate(BaseModel):
    status: Literal["in_progress", "completed"]


class WorkflowCloseOut(BaseModel):
    confirmed_root_cause: FailureMode
    alert_was_useful: bool
    comment: str | None = Field(default=None, max_length=1000)


class WorkflowMetricsRead(BaseModel):
    useful_alert_rate: float | None
    useful_alert_count: int
    usefulness_sample_count: int
    closed_work_order_count: int
    top_three_root_cause_accuracy: float | None
    root_cause_sample_count: int
    approval_to_dispatch_p95_seconds: float | None
    dispatch_sample_count: int
    simulated_dispatch_count: int
    targets: dict[str, float]
    status_counts: dict[str, int]


class WorkflowEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    work_order_id: uuid.UUID
    event_type: str
    actor_type: str
    actor_id: str | None
    actor_roles: list[str]
    actor_verified: bool
    from_status: str | None
    to_status: str | None
    details: dict[str, Any]
    previous_hash: str | None
    event_hash: str
    created_at: datetime
