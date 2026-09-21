"""Pure enum-driven projection from the engine protocol into UI ownership."""
from __future__ import annotations
from enum import IntEnum
from vmodel_contracts.interactive import EngineSnapshot,InputKind,NextStep,ProcessStatus

class UiMode(IntEnum):
    EXECUTING=10
    TEXT_INPUT=20
    CHOICE_INPUT=21
    APPROVAL_INPUT=22
    COMPLETE=30
    ERROR=40
    RESTART_REQUIRED=50

_INPUT_MODES={InputKind.TEXT:UiMode.TEXT_INPUT,InputKind.CHOICE:UiMode.CHOICE_INPUT,InputKind.APPROVAL:UiMode.APPROVAL_INPUT}

def project(snapshot:EngineSnapshot)->UiMode:
    if snapshot.next_step==NextStep.RESTART_REQUIRED:return UiMode.RESTART_REQUIRED
    if snapshot.process_status==ProcessStatus.FAILED:return UiMode.ERROR
    if snapshot.process_status==ProcessStatus.COMPLETED:return UiMode.COMPLETE
    if snapshot.next_step==NextStep.WAIT_INPUT:
        if snapshot.open_input is None:return UiMode.ERROR
        return _INPUT_MODES[snapshot.open_input.kind]
    return UiMode.EXECUTING
