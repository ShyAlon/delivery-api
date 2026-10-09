"""Pure recovery planning: exactly one owner/action for each durable task snapshot."""
from __future__ import annotations
from dataclasses import dataclass
from .phases import PHASE_DEFINITIONS, successor

@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    action: str
    phase: str | None = None
    target: str | None = None
    reason: str = ""

IDLE=RecoveryPlan("idle",reason="no recoverable work")

def plan_recovery(task:dict, latest_event:dict|None=None, *, has_session:bool=False)->RecoveryPlan:
    phases=task.get("phases",[]);states={p["phase_key"]:p["state"] for p in phases}
    approved=[i for i,p in enumerate(phases) if p.get("state") in ("approved","done")]
    if approved:
        furthest=max(approved)
        contradictory=next((p for p in phases[:furthest] if p.get("state") not in ("approved","done")),None)
        if contradictory:return RecoveryPlan("reconcile",contradictory["phase_key"],phases[furthest]["phase_key"],"downstream approval proves prefix")
    # A durable external-input requirement owns recovery even when later noise
    # (for example an obsolete process completion) was appended after it.
    events=task.get("events",[])
    requirement_event=next((event for event in reversed(events) if event.get("kind")=="requirement" and (event.get("payload") or {}).get("kind")=="external_input"),None)
    if requirement_event:
        requirement_id=requirement_event.get("id",0)
        resolved=any(event.get("kind")=="baseline" and event.get("id",0)>requirement_id and (event.get("payload") or {}).get("resolved") for event in events)
        if not resolved:return RecoveryPlan("restore_wait",requirement_event.get("phase_key"),reason="durable external input requirement")
    active_phase=task.get("active_phase")
    if active_phase and states.get(active_phase)=="awaiting_approval":
        gate=next((gate for gate in task.get("gates",[]) if gate.get("after_phase")==active_phase),None)
        if gate and gate.get("state")!="decided":return RecoveryPlan("restore_gate",active_phase,reason="generated phase is awaiting founder approval")
    for definition in reversed(PHASE_DEFINITIONS):
        target=successor(definition.key)
        if target and states.get(definition.key) in ("approved","done") and states.get(target.key) in ("pending","running"):
            return RecoveryPlan("launch_successor",definition.key,target.key,"completed boundary has no owner")
    latest_event=latest_event or {}
    interview_complete=all(states.get(key) in ("approved","done") for key in ("intent","conops"))
    legacy=task.get("state")=="failed" and latest_event.get("kind")=="error" and latest_event.get("payload",{}).get("exit_code") in (-15,143)
    if has_session and not interview_complete and (task.get("state") in ("running","waiting_gate") or legacy):
        return RecoveryPlan("resume","intent",reason="interview checkpoint is current")
    return IDLE


def flag_orphaned_running(store,task_id,*,evidence):
    """Flag a verified missing live owner, not a transport outage or Stop.

    Review-only event. Never starts work, kills calls, changes Stop, or declares
    provider quiescence. Idempotent for the current generation/active phase.
    """
    if evidence not in ('no_session_or_worker','engine_session_not_found'):
        raise ValueError('verified absent execution owner required')
    task=store.task(task_id)
    if not task or task['state']!='running':return False
    phase=task.get('active_phase');generation=task.get('generation',1)
    identity={'source':'startup_orphan_review','generation':generation,'phase':phase,'evidence':evidence,'auto_restart':False}
    if any(e.get('kind')=='orphaned_running' and e.get('payload')==identity for e in store.events(task_id)):
        return False
    store.add_event(task_id,'orphaned_running',f'Task {task_id} is marked running but has no verified live session or worker. Review required; no automatic restart.',phase_key=phase,payload=identity)
    return True


def orphan_review_items(store):
    """Owner-scoped/admin-authorized service projection, not global SQL."""
    result=[]
    for task in store.list_tasks():
        if task['state']!='running':continue
        flags=[e for e in store.events(task['task_id']) if e.get('kind')=='orphaned_running'
               and e.get('payload',{}).get('generation')==task.get('generation',1)
               and e.get('payload',{}).get('phase')==task.get('active_phase')]
        if flags:
            result.append({'task_id':task['task_id'],'state':task['state'],'active_phase':task.get('active_phase'),
                'generation':task.get('generation',1),'updated_at':task['updated_at'],
                'flag_event_id':flags[-1]['id'],'evidence':flags[-1]['payload']['evidence'],
                'review_required':True,'cleanup_available':False,
                'reason':'A startup flag is not current quiescence proof; recheck execution ownership before cleanup.'})
    return result
