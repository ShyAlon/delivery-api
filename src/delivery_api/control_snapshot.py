"""One UI control shape, with source-owned preconditions kept opaque.

This is a projection, not a second authority. Progress never changes it.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from vmodel_contracts.interactive import EngineSnapshot, InputKind, ProcessStatus, NextStep

class TaskControlSnapshot(BaseModel):
    model_config=ConfigDict(extra="forbid")
    version:Literal[2]=2
    task_id:str
    phase_key:str|None
    mode:Literal["not_started","working","waiting_input","waiting_gate","ready","completed","failed","stop_requested","stopped"]
    owner:Literal["engine","delivery","none"]
    wait_id:str|None=None
    kind:str|None=None
    prompt:str|None=None
    options:list[dict]=Field(default_factory=list)
    artifact_ref:str|None=None
    actions:list[str]=Field(default_factory=list)
    precondition:dict=Field(default_factory=dict)
    revision:str
    process_live:bool=False
    decision:dict|None=None
    gate:dict|None=None
    diagnostic:dict|None=None
    input_id:str|None=None

    @model_validator(mode="after")
    def wait_has_owner(self):
        if self.mode.startswith("waiting_") and (not self.wait_id or self.owner=="none"):
            raise ValueError("a wait requires a source and stable ID")
        return self


def project(task:dict,input_state:dict,engine:EngineSnapshot|None=None,work=None,artifacts:list[dict]|None=None,ready_next:str|None=None)->TaskControlSnapshot:
    task_id=task["task_id"];phase=task.get("active_phase");state=task.get("state")
    # Only durable delivery reconciliation can call the whole task stopped.
    # Engine STOPPED says nothing about producer jobs or held provider usage.
    engine_stop=bool(engine and engine.task_id==task_id and
                     engine.process_status in (ProcessStatus.STOP_REQUESTED,ProcessStatus.STOPPED))
    stopped=('stopped' if state=='stopped' and task.get('stopped_at') is not None
             else 'stop_requested' if state in ('stop_requested','stopped') or engine_stop else None)
    if stopped:
        return TaskControlSnapshot(task_id=task_id,phase_key=phase,mode=stopped,owner="none",
            revision=f"stop:{task.get('generation',0)}:{stopped}",process_live=False,
            actions=["delete"] if stopped=="stopped" and task.get("stopped_at") is not None else [],
            diagnostic={"reason":"accepted calls may still finish and bill; Delete waits for verified quiescence"} if stopped=="stop_requested" else None)
    process_live=bool(input_state.get("process_live"))
    diagnostic=None
    if state=="failed" or (state not in ("not_started","done") and
        (input_state.get("engine_unreachable") or input_state.get("engine_error") or
         (not process_live and not (work and work.status=="running") and
          not any(g.get("state")=="waiting" and g.get("after_phase")==phase for g in task.get("gates",[]))))):
        cause=input_state.get("engine_error")
        diagnostic=({"reason":"engine protocol rejected the snapshot","http_status":cause["http_status"],
                     "code":cause["code"],"detail":cause["detail"],"task_state":state,"active_phase":phase}
                    if cause else {"reason":"engine session completed" if input_state.get("engine_process_status")==40
                                   else "engine transport unavailable" if input_state.get("engine_unreachable")
                                   else "task has no live ActiveWork owner","task_state":state,"active_phase":phase})
    base=dict(task_id=task_id,phase_key=phase,mode="working",owner="delivery",
              revision=f"task:{task.get('updated_at',0)}",process_live=process_live,diagnostic=diagnostic)
    if state=="not_started":return TaskControlSnapshot(**{**base,"mode":"not_started","owner":"none"})
    if state=="done":return TaskControlSnapshot(**{**base,"mode":"completed","owner":"none"})
    if state=="failed":return TaskControlSnapshot(**{**base,"mode":"failed","owner":"none"})
    # Active subprocess decision wins over a stale stored gate for the same phase.
    if work and work.owner=="decision" and work.phase==phase and work.status=="waiting" and work.decision:
        decision=work.decision;key=str(decision.get("decision_id") or "")
        if key:return TaskControlSnapshot(**{**base,"mode":"waiting_input","owner":"delivery","wait_id":key,"decision":decision,
            "kind":"decision","prompt":decision.get("prompt"),"options":decision.get("options") or [],
            "actions":["answer"],"precondition":{"decision_id":key},"revision":f"decision:{key}"})
    if engine and phase in ("intent","conops") and engine.task_id==task_id and engine.process_status==ProcessStatus.WAITING_INPUT and engine.next_step==NextStep.WAIT_INPUT and engine.open_input:
        pending=engine.open_input;kind={InputKind.TEXT:"text",InputKind.CHOICE:"choice",InputKind.APPROVAL:"approval"}[pending.kind]
        if pending.context.get('decision_purpose')=='semantic_review':kind='semantic_review'
        return TaskControlSnapshot(**{**base,"mode":"waiting_input","owner":"engine","wait_id":str(pending.input_id),
            "kind":kind,"prompt":pending.prompt,"options":[option.model_dump(mode="json") for option in pending.options],
            "decision":input_state.get("decision") if kind!="text" else None,"input_id":str(pending.input_id),
            "actions":["answer","switch_model"],"precondition":{"session_id":engine.session_id,
                "input_id":str(pending.input_id),"session_sequence":engine.sequence},
            "revision":f"engine:{engine.session_id}:{engine.sequence}"})
    if process_live and input_state.get("waiting") and (input_state.get("input_id") or (input_state.get("decision") or {}).get("decision_id")):
        return TaskControlSnapshot(**{**base,"mode":"waiting_input","owner":"engine",
            "wait_id":str(input_state.get("input_id") or input_state["decision"]["decision_id"]),"kind":"choice" if input_state.get("decision") else "text",
            "decision":input_state.get("decision"),
            "prompt":input_state.get("question"),"input_id":str(input_state.get("input_id") or input_state["decision"]["decision_id"]),
            "actions":["answer"]})
    gate=next((g for g in task.get("gates",[]) if g.get("state")=="waiting" and g.get("after_phase")==phase),None)
    if gate:
        key=gate["gate_key"]
        evidence=next((artifact.get("ref") for artifact in (artifacts or [])
                       if artifact.get("phase_key")==phase and not artifact.get("stale")
                       and artifact.get("present",True) and artifact.get("valid") is not False),None)
        return TaskControlSnapshot(**{**base,"mode":"waiting_gate","owner":"delivery","wait_id":key,
            "artifact_ref":evidence,"gate":gate,
            "kind":"phase_gate","prompt":gate.get("label"),"options":[],"actions":["approve","edit","redo"],
            "precondition":{"gate_key":key,"gate_revision":gate.get("revision",0)},
            "revision":f"gate:{key}:{gate.get('revision',0)}"})
    if ready_next and not process_live:
        # The interview is done and nothing is running: the owner starts the next phase with a click.
        return TaskControlSnapshot(**{**base,"mode":"ready","owner":"delivery","kind":"next_phase","diagnostic":None,
            "prompt":"The next phase is ready to start","actions":["start_next_phase"],"precondition":{"phase":ready_next},
            "revision":f"ready:{ready_next}:{task.get('updated_at',0)}"})
    if diagnostic:return TaskControlSnapshot(**{**base,"mode":"failed","owner":"none"})
    return TaskControlSnapshot(**base)
