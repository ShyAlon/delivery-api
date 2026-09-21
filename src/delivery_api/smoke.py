"""Deterministic typed answers for the packaged real-model smoke."""
from __future__ import annotations
from vmodel_contracts.interactive import InputKind


def typed_smoke_answer(decision:dict)->str:
    """Return a contract-valid answer without inspecting human prompt text."""
    kind=InputKind(decision["kind"])
    options=decision.get("options") or []
    if kind==InputKind.APPROVAL:
        value=next((option["value"] for option in options if option["value"]==1),options[0]["value"])
    elif kind==InputKind.CHOICE:
        value=options[0]["value"]
    else:
        return "Two adults use it weekly at home. Success means every chore has one owner and due date, and both people can see completion history."
    if decision.get("rationale_required"):
        return f"{value} - This option best fits the stated users, operating setting, and measurable success outcome."
    return str(value)


def load_smoke_checkpoint(path):
    import json
    from pathlib import Path
    checkpoint=Path(path)
    if not checkpoint.exists():return None
    data=json.loads(checkpoint.read_text())
    required={'task_id','model_route','answers','status'}
    return data if required <= data.keys() else None

def save_smoke_checkpoint(path,data):
    import json
    from pathlib import Path
    checkpoint=Path(path);checkpoint.parent.mkdir(parents=True,exist_ok=True)
    temporary=checkpoint.with_suffix('.tmp');temporary.write_text(json.dumps(data,sort_keys=True));temporary.replace(checkpoint)
