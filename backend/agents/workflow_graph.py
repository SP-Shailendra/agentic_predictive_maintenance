from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.compliance_agent import evaluate_compliance
from agents.diagnostic_agent import DiagnosisResult, diagnose_alert
from agents.state import WorkflowState


def _activity(state: WorkflowState, agent: str, message: str, status: str = "complete") -> list[dict[str, Any]]:
    return [*state.get("activity", []), {
        "agent": agent,
        "message": message,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }]


async def detect_node(state: WorkflowState) -> dict[str, Any]:
    return {
        "detection_confidence": state.get("detection_confidence", 0.94),
        "activity": _activity(state, "Detect", f"Alert classified: {state.get('alert_type', 'equipment anomaly')} ({state.get('severity', 'high')})."),
    }


async def diagnose_node(state: WorkflowState) -> dict[str, Any]:
    if state.get("diagnosis") and state.get("recommendation"):
        result = DiagnosisResult(
            diagnosis=state["diagnosis"],
            confidence=state.get("diagnosis_confidence", 0.5),
            recommendation=state["recommendation"],
            failure_modes=state.get("failure_modes", []),
            evidence=state.get("evidence", []),
            evidence_sources=state.get("evidence_sources", []),
            evidence_status=state.get("evidence_status", "legacy"),
            evidence_error_code=state.get("evidence_error_code"),
            citation_verification_status=state.get("citation_verification_status", "unavailable"),
            citation_checks=state.get("citation_checks", []),
            method=state.get("diagnostic_method", "threshold_fallback"),
            model_version=state.get("diagnostic_model_version", "threshold-fallback-v1"),
        )
        log_message = "Persisted diagnosis loaded for approval resumption."
    else:
        result = await diagnose_alert(state)
        log_message = f"Assessment complete ({result.confidence:.0%} confidence)."
    return {
        "diagnosis": result.diagnosis,
        "diagnosis_confidence": result.confidence,
        "recommendation": result.recommendation,
        "failure_modes": result.failure_modes,
        "evidence": result.evidence,
        "evidence_sources": result.evidence_sources,
        "evidence_status": result.evidence_status,
        "evidence_error_code": result.evidence_error_code,
        "citation_verification_status": result.citation_verification_status,
        "citation_checks": result.citation_checks,
        "diagnostic_method": result.method,
        "diagnostic_model_version": result.model_version,
        "activity": _activity(state, "Diagnose", log_message),
    }


async def decide_node(state: WorkflowState) -> dict[str, Any]:
    compliance = await evaluate_compliance(state)
    return {
        "compliance_check": compliance,
        "requires_approval": True,
        "status": "awaiting_approval" if compliance.get("compliant") else "blocked",
        "activity": _activity(state, "Decide", str(compliance.get("reason", "Policy check complete."))),
    }


async def dispatch_node(state: WorkflowState) -> dict[str, Any]:
    compliance_check = state.get("compliance_check") or {}
    if not compliance_check.get("compliant"):
        return {
            "status": "blocked",
            "activity": _activity(state, "Dispatch", "Policy check failed; dispatch blocked.", "error"),
        }
    if not state.get("approved"):
        return {
            "status": "awaiting_approval",
            "activity": _activity(state, "Dispatch", "Dispatch held until an authorized human approves.", "waiting"),
        }
    return {
        "status": "dispatch_queued",
        "activity": _activity(state, "Dispatch", "Approved work order queued for CMMS delivery."),
    }


async def document_node(state: WorkflowState) -> dict[str, Any]:
    document = (
        f"Asset {state.get('asset_tag', state.get('asset_id', 'unknown'))}: {state.get('diagnosis', 'No diagnosis recorded')} "
        f"Recommendation: {state.get('recommendation', 'None')} Status: {state.get('status', 'unknown')}."
    )
    return {
        "document": document,
        "activity": _activity(state, "Document", "Decision record and agent trace persisted to the work order."),
    }


def build_workflow_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("detect", detect_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("decide", decide_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("document", document_node)
    graph.add_edge(START, "detect")
    graph.add_edge("detect", "diagnose")
    graph.add_edge("diagnose", "decide")
    graph.add_edge("decide", "dispatch")
    graph.add_edge("dispatch", "document")
    graph.add_edge("document", END)
    return graph.compile()


workflow_graph = build_workflow_graph()
