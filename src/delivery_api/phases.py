"""Canonical, immutable configuration for the Requi.re phase pipeline."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class PhaseDefinition:
    key: str
    label: str
    executor: str
    generator_stage: str | None = None
    prerequisite: str | None = None
    artifacts: tuple[str, ...] = ()

PHASE_DEFINITIONS = (
    PhaseDefinition("intent", "Start", "interview", artifacts=("intent_model.json",)),
    PhaseDefinition("conops", "Concept of operations", "interview", artifacts=("conops.json",)),
    PhaseDefinition("needs", "Stakeholder needs", "generator", "needs", artifacts=("needs.json",)),
    PhaseDefinition("srs", "Requirements", "generator", "srs", artifacts=("srs.json", "srs.semantic-review.json")),
    PhaseDefinition("architecture", "Architecture", "generator", "architecture", "system-baseline", ("architecture.json", "architecture.decisions.json")),
    PhaseDefinition("specs", "Item specifications", "generator", "items", artifacts=("items.json", "spec_items.json")),
    PhaseDefinition("tests", "Verification", "generator", "tests", artifacts=("tests.json", "test_specifications.json")),
    PhaseDefinition("validation", "Validation", "generator", "validation", artifacts=("validation.json",)),
)
PHASE_BY_KEY = {phase.key: phase for phase in PHASE_DEFINITIONS}
PHASE_KEYS = tuple(phase.key for phase in PHASE_DEFINITIONS)
PHASE_POSITIONS = {phase.key: index for index, phase in enumerate(PHASE_DEFINITIONS)}

def successor(key: str) -> PhaseDefinition | None:
    index = PHASE_POSITIONS[key] + 1
    return PHASE_DEFINITIONS[index] if index < len(PHASE_DEFINITIONS) else None

def predecessors(key: str) -> tuple[PhaseDefinition, ...]:
    return PHASE_DEFINITIONS[:PHASE_POSITIONS[key]]

def followers(key: str) -> tuple[PhaseDefinition, ...]:
    return PHASE_DEFINITIONS[PHASE_POSITIONS[key] + 1:]
