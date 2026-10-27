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


def model_calls(root,cloud,limit=300):
    """Per-call model timing for the Debug window: every llm_call_end in the
    session's llm-calls.jsonl plus a per-call-type aggregate - which types of
    calls took how long. Cloud mirrors the provider-diagnostics privacy rule:
    token counts and derived rates stay server-side; durations stay visible."""
    import json as _json
    path=root/"llm-calls.jsonl"
    calls=[]
    if path.is_file():
        with path.open(errors="replace") as stream:
            for line in stream:
                try:entry=_json.loads(line)
                except ValueError:continue
                if entry.get("event")!="llm_call_end":continue
                call={key:entry[key] for key in ("ts","stage","item","provider","model","latency_s","keep_alive") if key in entry}
                timing=entry.get("timing")
                if isinstance(timing,dict):
                    if cloud:timing={k:v for k,v in timing.items() if not k.endswith("_count")}
                    if timing:call["timing"]=timing
                if not cloud:
                    if entry.get("usage"):call["usage"]=entry["usage"]
                    if entry.get("tokens_per_s") is not None:call["tokens_per_s"]=entry["tokens_per_s"]
                calls.append(call)
        calls=calls[-limit:]
    by_stage={}
    for call in calls:
        key=(call.get("provider"),call.get("model"),call.get("stage"))
        agg=by_stage.setdefault(key,{"provider":key[0],"model":key[1],"stage":key[2],"calls":0,"total_s":0.0})
        agg["calls"]+=1;agg["total_s"]=round(agg["total_s"]+call.get("latency_s",0.0),2)
        timing=call.get("timing") or {}
        for k in ("load_ms","prompt_eval_ms","eval_ms"):
            if k in timing:agg[k]=agg.get(k,0)+timing[k]
        if not cloud and call.get("usage"):
            agg["input_tokens"]=agg.get("input_tokens",0)+call["usage"].get("input_tokens",0)
            agg["output_tokens"]=agg.get("output_tokens",0)+call["usage"].get("output_tokens",0)
    aggregates=[]
    for agg in by_stage.values():
        agg["mean_s"]=round(agg["total_s"]/agg["calls"],2)
        aggregates.append(agg)
    aggregates.sort(key=lambda a:-a["total_s"])
    return {"calls":calls,"by_stage":aggregates}


def build(task,events,control,input_state,engine,limit=EVENT_LIMIT,worker_state=None,model_calls=None):
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
    if model_calls is not None:out["model_calls"]=model_calls
    return out
