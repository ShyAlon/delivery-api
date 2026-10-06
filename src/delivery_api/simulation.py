"""Validated, zero-process replay of recorded V-model runs."""
from __future__ import annotations
import asyncio,json,re,tempfile,os
from datetime import datetime
from pathlib import Path
from .store import PHASES,Storage
from vmodel_contracts import CANONICAL_STAGE_IDS

ARTIFACTS={"needs":"needs.json","srs":"srs.json","architecture":"architecture.json","specs":"items.json","tests":"tests.json","validation":"validation.json"}
_ALLOWED_STAGES=CANONICAL_STAGE_IDS

class ImportValidationError(ValueError):
    def __init__(self,error,cause,next_action):super().__init__(error);self.cause=cause;self.next_action=next_action

def _fail(error,cause,next_action):raise ImportValidationError(error,cause,next_action)

def validate_recorded_run(source:Path)->tuple[list[dict],str]:
    source=source.expanduser().resolve()
    if not source.is_dir():_fail("The selected folder is not a directory.",f"No directory exists at {source}.","Choose the artifact folder produced by a completed or interrupted V-model run.")
    # A unified session root may contain logs in interview/ and spec/.
    candidates=[source/"usage.jsonl",source/"interview"/"usage.jsonl",source/"spec"/"usage.jsonl"]
    usage=next((p for p in candidates if p.is_file()),candidates[0])
    if not usage.is_file():_fail("The selected folder has no usage.jsonl.","It is not a recorded V-model run, or the recording is incomplete.","Choose the run's artifact folder, not an individual artifact file.")
    if usage.stat().st_size>20_000_000:_fail("usage.jsonl is too large to import.","The 20 MB safety limit was exceeded.","Choose the original run log or archive this file before importing.")
    rows=[];session=None
    for number,line in enumerate(usage.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip():continue
        try:row=json.loads(line)
        except json.JSONDecodeError as e:_fail(f"usage.jsonl line {number} is invalid JSON.",str(e),"Repair or re-export the recorded run, then select it again.")
        if not isinstance(row,dict):_fail(f"usage.jsonl line {number} is not an object.","Recorded events must be JSON objects.","Re-export the run with the current recorder.")
        stage=row.get("stage")
        if stage not in _ALLOWED_STAGES:_fail(f"usage.jsonl line {number} has an unknown stage.",f"stage={stage!r}",f"Use one of: {', '.join(sorted(_ALLOWED_STAGES))}.")
        for key in ("provider","model","ts"):
            if not isinstance(row.get(key),str) or not row[key].strip():_fail(f"usage.jsonl line {number} is missing {key}.","The event shape is incomplete.","Re-export the run with the current recorder.")
        try:datetime.fromisoformat(row["ts"].replace("Z","+00:00"))
        except ValueError:_fail(f"usage.jsonl line {number} has an invalid timestamp.",f"ts={row['ts']!r}","Re-export the run with ISO-8601 timestamps.")
        for key in ("prompt_tokens","completion_tokens"):
            value=row.get(key,0)
            if not isinstance(value,int) or value<0:_fail(f"usage.jsonl line {number} has invalid {key}.",f"{key} must be a non-negative integer.","Repair or re-export the recording.")
        sid=row.get("session_id")
        if sid:
            if session and sid!=session:_fail("The recording mixes session IDs.",f"Found both {session!r} and {sid!r}.","Choose one run directory; do not combine usage logs.")
            session=sid
        rows.append(row)
    if not rows:_fail("usage.jsonl has no events.","The recording is empty.","Choose a run that reached at least one model stage.")
    # Every present artifact must parse and satisfy its current contract.
    from vmodel_contracts import ArchitectureOverview,SRSDocument,StakeholderNeedsSet
    validators={"needs.json":StakeholderNeedsSet.model_validate,"srs.json":SRSDocument.model_validate,"architecture.json":ArchitectureOverview.model_validate}
    for name,validate in validators.items():
        path=source/name
        if not path.exists():continue
        try:data=json.loads(path.read_text(encoding="utf-8"));validate(data)
        except Exception as e:_fail(f"{name} is not compatible with current contracts.",f"{type(e).__name__}: {str(e)[:300]}","Open or regenerate this run with current V-model tools, then import it again.")
    for name in ("items.json","tests.json"):
        path=source/name
        if path.exists():
            try:data=json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:_fail(f"{name} is invalid JSON.",str(e),"Regenerate the recorded artifact, then import again.")
            if not isinstance(data,list):_fail(f"{name} has the wrong shape.","Expected a JSON array.","Regenerate it with the current spec generator.")
    return rows,session or source.name

class Simulation:
    def __init__(self,store:Storage):self.store,self.running=store,{}
    def import_run(self,source:Path,title:str|None=None)->dict:
        source=source.expanduser().resolve();rows,sid=validate_recorded_run(source)
        # Unified session imports can name themselves from their interview intent.
        # A browser upload contains only usage.jsonl, so it safely falls back to
        # the session ID unless the caller supplied a title.
        inferred=None
        from vmodel_contracts.runtime import artifacts_root
        canonical_intent = artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")) / sid / "interview" / "intent_model.json"
        for intent_path in (source/"intent_model.json", source/"interview"/"intent_model.json", canonical_intent):
            if not intent_path.is_file(): continue
            try:
                intent=json.loads(intent_path.read_text(encoding="utf-8"))
                inferred=next((str(intent.get(k)).strip() for k in ("problem_statement","core_value_proposition")
                               if isinstance(intent.get(k),str) and str(intent.get(k)).strip()),None)
            except (OSError,json.JSONDecodeError):
                pass
            if inferred: break
        task=self.store.create_task(title or inferred or f"Recorded run {sid}",f"Simulation from {source}")
        models=sorted({f"{r['provider']}/{r['model']}" for r in rows})
        self.store.configure_simulation(task["task_id"],str(source),", ".join(models));self.store.audit("local user","simulation.imported",task["task_id"],{"source":str(source),"events":len(rows),"validated":True});return self.store.task(task["task_id"])
    async def replay(self,task_id:str,speed:float|None):
        task=self.store.task(task_id)
        if not task or not task.get("simulated"):raise ValueError("task is not a simulation")
        rows,_=validate_recorded_run(Path(task["source_dir"]));previous=None
        for key,_ in PHASES:self.store.update_phase(task_id,key,"pending")
        for i,row in enumerate(rows):
            phase={"review":"tests","items":"specs"}.get(row["stage"],row["stage"])
            if phase not in dict(PHASES):continue
            if previous and previous!=phase:
                self.store.update_phase(task_id,previous,"done",detail="Recorded phase complete");gate=self.store.wait_at_gate(task_id,previous)
                if gate:
                    self.store.add_event(task_id,"gate","Simulation paused at closed gate",phase_key=previous,payload={"gate_key":gate["gate_key"]})
                    while self.store.task(task_id)["state"]=="waiting_gate":await asyncio.sleep(.05)
            self.store.update_phase(task_id,phase,"running",detail=f"Recorded {row['provider']}/{row['model']} call {i+1}/{len(rows)}",completed=i+1,total=len(rows));self.store.add_event(task_id,"progress",f"[{phase}] {row.get('prompt_tokens',0)} input + {row.get('completion_tokens',0)} output tokens",phase_key=phase,payload={"simulated":True,"recorded_ts":row["ts"]});previous=phase
        if previous:self.store.update_phase(task_id,previous,"done",detail="Replay complete")
        source=Path(task["source_dir"])
        # Recordings made before the Validation phase end at Verification. They stay complete.
        if previous=="tests" and not (source/"validation.json").exists():
            self.store.update_phase(task_id,"validation","done",detail="Not part of this recording")
        for phase,name in ARTIFACTS.items():
            if (source/name).exists():self.store.update_phase(task_id,phase,"done",detail="Imported recorded artifact");self.store.add_task_artifact(task_id,phase,name,str(source/name))
        # A recording may end after any phase. Exhausting its rows ends the
        # replay worker, even if the last V-process phase was not recorded.
        # Do not leave that task marked running forever, or claim that its
        # unfinished product definition is complete.
        if self.store.task(task_id)["state"]=="running" and previous:
            self.store.update_phase(task_id,previous,"failed",detail="Recording ended before the V-process was complete")
            self.store.add_event(task_id,"interrupted","The recording ended before the V-process was complete; no replay is running.",phase_key=previous,
                                 payload={"next":"Import a complete recording or delete this partial replay."})
        if self.store.task(task_id)["state"]=="done":
            self.store.audit("local user","simulation.completed",task_id,{"speed":"instant" if speed is None else speed})
            self.store.add_event(task_id,"complete","Simulation replay complete - no process was spawned",phase_key=previous)
        else:
            self.store.audit("local user","simulation.partial",task_id,{"speed":"instant" if speed is None else speed})
    def reconcile_interrupted(self):
        """An in-memory replay cannot survive a server process restart."""
        for task in self.store.list_tasks():
            if not task.get("simulated") or task["state"] not in ("running", "waiting_gate"):
                continue
            active=self.running.get(task["task_id"])
            if active is not None and not active.done():
                continue
            phase=task.get("active_phase")
            if not phase:continue
            self.store.add_event(task["task_id"],"interrupted",
                                 "Recorded replay stopped when the server restarted. Its source recording is intact; start replay again if needed.",
                                 phase_key=phase,payload={"cause":"replay worker is no longer running","next":"Start the replay again, or delete this stopped task."})
            self.store.update_phase(task["task_id"],phase,"failed",detail="Recorded replay stopped on server restart")
            self.store.audit("system","simulation.interrupted",task["task_id"],{"phase":phase,"source_artifacts_deleted":False})

    def start(self,task_id:str,speed:float|None):
        if task_id in self.running and not self.running[task_id].done():raise ValueError("simulation already running")
        self.running[task_id]=asyncio.create_task(self.replay(task_id,speed))
