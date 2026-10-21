"""Deterministic checks shared by future planning workflows."""

REQUIRED_STEP_FIELDS = ("target", "operation", "read_write", "verification")


def validate_plan(plan: object) -> dict:
    errors: list[dict[str, object]] = []
    if not isinstance(plan, dict):
        return {"valid": False, "execution_authorized": False, "errors": [{"path": "plan", "message": "must be an object"}]}
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"valid": False, "execution_authorized": False, "errors": [{"path": "steps", "message": "must be a non-empty array"}]}
    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append({"path": path, "message": "must be an object"})
            continue
        for field in REQUIRED_STEP_FIELDS:
            value = step.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append({"path": f"{path}.{field}", "message": "is required"})
        if step.get("read_write") not in ("read", "write"):
            errors.append({"path": f"{path}.read_write", "message": "must be read or write"})
        if step.get("read_write") == "write" and step.get("approval_required") is not True:
            errors.append({"path": f"{path}.approval_required", "message": "write steps require approval_required=true"})
    return {"valid": not errors, "execution_authorized": False, "errors": errors}
