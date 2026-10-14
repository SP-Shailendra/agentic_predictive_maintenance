from agents.state import WorkflowState


async def evaluate_compliance(state: WorkflowState) -> dict[str, object]:
    checks = [
        "Maintenance recommendation is advisory and does not directly control equipment.",
        "A qualified human must approve dispatch of the proposed work order.",
    ]
    if state.get("severity") == "critical":
        checks.append("Critical-severity alerts require the site's emergency response procedure in parallel.")
    if not state.get("diagnosis") or not state.get("recommendation"):
        return {"compliant": False, "checks": checks, "reason": "Diagnosis and recommendation are required."}
    return {"compliant": True, "checks": checks, "reason": "Human approval required before dispatch."}
