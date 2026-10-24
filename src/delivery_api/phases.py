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
    # Declared companions that exist only when the phase produced them (the
    # generator's own completion report includes them conditionally): the srs
    # semantic-review record exists when a semantic review ran, and the
    # architecture decisions companion exists only with research_required stack
    # slots. They cross hosts and are pinned when present, but no evidence set
    # may be forced to carry them when the phase legitimately produced none.
    conditional_artifacts: tuple[str, ...] = ()

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        return tuple(name for name in self.artifacts if name not in self.conditional_artifacts)

PHASE_DEFINITIONS = (
    PhaseDefinition("intent", "Start", "interview", artifacts=("intent_model.json",)),
    PhaseDefinition("conops", "Concept of operations", "interview", artifacts=("conops.json",)),
    PhaseDefinition("needs", "Stakeholder needs", "generator", "needs", artifacts=("needs.json",)),
    PhaseDefinition("srs", "Requirements", "generator", "srs", artifacts=("srs.json", "srs.semantic-review.json"),
                    conditional_artifacts=("srs.semantic-review.json",)),
    PhaseDefinition("architecture", "Architecture", "generator", "architecture", "system-baseline", ("architecture.json", "architecture.decisions.json"),
                    conditional_artifacts=("architecture.decisions.json",)),
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
