"""Owner-scoped task diagnostics for the Debug window.

A pure projection of data the caller already read for its own task: no I/O, no
second authority. The caller decides who may see it (the task read is already
owner-scoped); this module only shapes the text so one Copy gives a developer
the whole picture: the question, the control state, the engine state and the
last events.
"""
from __future__ import annotations

EVENT_LIMIT=30
_TEXT_LIMIT=4000


def _clip(value,limit=_TEXT_LIMIT):
    if isinstance(value,str) and len(value)>limit:return value[:limit]+f"... [{len(value)-limit} more characters]"
    return value


def _name(value):
    return getattr(value,"name",None) or str(value)


def engine_summary(snapshot=None,error=None):
    """What the interview engine says about this task's session right now."""
    if error:return {"available":False,"error":str(error)}
    if snapshot is None:return {"available":False,"error":"this task has no engine session"}
    pending=getattr(snapshot,"open_input",None);open_input=None
    if pending is not None:
        options=[]
        for option in getattr(pending,"options",None) or []:
            options.append(option.model_dump(mode="json") if hasattr(option,"model_dump") else str(option))
        open_input={"input_id":str(getattr(pending,"input_id","")),"kind":_name(getattr(pending,"kind","")),
                    "prompt":getattr(pending,"prompt",None),"options":options,
                    "rationale_required":bool(getattr(pending,"rationale_required",False))}
    return {"available":True,"session_id":str(getattr(snapshot,"session_id","")),
            "process_status":int(snapshot.process_status),"process_status_name":_name(snapshot.process_status),
            "next_step":_name(getattr(snapshot,"next_step","")),"sequence":getattr(snapshot,"sequence",None),
            "open_input":open_input}


def event_row(event):
    row={key:event.get(key) for key in ("id","ts","created_at","kind","phase_key","message","payload") if key in event}
    row["message"]=_clip(row.get("message"))
    return row


def _when(value):
    if not isinstance(value,(int,float)):return value
    from datetime import datetime,timezone
    return datetime.fromtimestamp(value,timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def worker_state_summary(worker_state):
    """The Stop-settle state for this task: worker jobs and provider attempts.

    A session parked in stop-requested leaves its job 'running' forever and Stop waits
    on it; before this section the only way to see that was a database console. Times
    are rendered UTC; payloads (options, quotes, usage) never leave the store."""
    if worker_state is None:return {"available":False}
    jobs=[{**{k:row.get(k) for k in ("job_id","kind","state","attempt","session_id","generation")},
           "created_at":_when(row.get("created_at")),"updated_at":_when(row.get("updated_at"))}
          for row in worker_state.get("worker_jobs") or []]
    attempts=[{**{k:row.get(k) for k in ("attempt_id","job_id","state","expense_reserved","retail_reserved","expense_actual","retail_actual")},
               "created_at":_when(row.get("created_at")),"updated_at":_when(row.get("updated_at"))}
              for row in worker_state.get("provider_attempts") or []]
    return {"available":True,"worker_jobs":jobs,"provider_attempts":attempts}


def build(task,events,control,input_state,engine,limit=EVENT_LIMIT,worker_state=None):
    """One JSON-able document. `question` is the text the owner should be seeing."""
    control=control or {};input_state=input_state or {}
    question=control.get("prompt") or input_state.get("question") or ((input_state.get("decision") or {}).get("prompt"))
    if not question and (engine or {}).get("open_input"):question=engine["open_input"].get("prompt")
    shown=list(events or [])[-limit:]
    out={"task":{key:task.get(key) for key in ("task_id","title","state","active_phase","generation") if key in task},
            "question":question,
            "control_snapshot":control,
            "input_state":input_state,
            "engine":engine,
            "events":{"total":len(events or []),"shown":len(shown),"items":[event_row(e) for e in shown]}}
    if worker_state is not None:out["worker_state"]=worker_state_summary(worker_state)
    return out
