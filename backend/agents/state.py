from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    alert_id: str
    asset_id: str
    asset_name: str
    asset_tag: str
    asset_type: str
    location: str
    plant_id: str
    knowledge_roles: list[str]
    alert_type: str
    severity: str
    message: str
    telemetry: dict[str, float]
    vibration_threshold_mm_s: float
    temperature_threshold_c: float
    vibration_threshold_source: str
    temperature_threshold_source: str
    detection_confidence: float
    diagnosis: str
    diagnosis_confidence: float
    diagnostic_method: str
    diagnostic_model_version: str
    recommendation: str
    failure_modes: list[str]
    evidence: list[str]
    evidence_sources: list[dict[str, Any]]
    evidence_status: str
    evidence_error_code: str
    compliance_check: dict[str, Any]
    requires_approval: bool
    approved: bool
    approval_comment: str
    status: str
    activity: list[dict[str, Any]]
    cmms_reference: str
    document: str
