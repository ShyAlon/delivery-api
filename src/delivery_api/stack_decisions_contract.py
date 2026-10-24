"""Structural validation for architecture.decisions.json - the founder
stack-decision companion carried across hosts as published approval evidence.

Mirrors the consumer invariants in app.py (_read_stack_decisions, the
stack-decisions endpoint, _open_stack_slots): the architecture revision the
decisions were recorded against, a slots list with stable slot-key identity,
and founder decisions that always carry non-empty text. Extra fields pass so
the generator owns the shape's evolution; the consumer's required reads are
never weakened. No sibling schema change.
"""

from pydantic import BaseModel, ConfigDict, Field


class FounderDecision(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str = Field(min_length=1, max_length=2000)
    reason: str = Field(default="", max_length=1000)
    actor: str = Field(min_length=1, max_length=200)
    at: str = Field(min_length=1, max_length=64)


class StackDecisionSlot(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(min_length=1, max_length=200)
    founder_decision: FounderDecision | None = None
    technical_choice: dict | None = None
    prefill: dict | None = None


class StackDecisionsCompanion(BaseModel):
    model_config = ConfigDict(extra="allow")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    slots: list[StackDecisionSlot]
