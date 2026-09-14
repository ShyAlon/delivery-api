"""Zero-process replay of recorded V-model runs."""
from __future__ import annotations
import asyncio, json
from datetime import datetime
from pathlib import Path
from .store import PHASES, Storage

ARTIFACTS={"needs":"needs.json","srs":"srs.json","architecture":"architecture.json","specs":"items.json","tests":"tests.json"}

class Simulation:
    def __init__(self, store:Storage): self.store,self.running=store,{}
    def import_run(self, source:Path, title:str|None=None)->dict:
        source=source.expanduser().resolve()
        if not source.is_dir(): raise ValueError(f"run directory not found: {source}")
        usage=source/"usage.jsonl"
        if not usage.is_file(): raise ValueError(f"recorded usage not found: {usage}")
        rows=[]
        for line in usage.read_text().splitlines():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: continue
        if not rows: raise ValueError("usage.jsonl has no readable events")
        sid=rows[0].get("session_id",source.name)
        task=self.store.create_task(title or f"Recorded run {sid}",f"Simulation from {source}")
        models=sorted({f"{r.get('provider','?')}/{r.get('model','?')}" for r in rows})
        self.store.configure_simulation(task["task_id"],str(source),", ".join(models))
        self.store.audit("local user","simulation.imported",task["task_id"],{"source":str(source),"events":len(rows)})
        return self.store.task(task["task_id"])
    async def replay(self, task_id:str, speed:float|None):
        task=self.store.task(task_id)
        if not task or not task.get("simulated"): raise ValueError("task is not a simulation")
        rows=[json.loads(x) for x in (Path(task["source_dir"])/"usage.jsonl").read_text().splitlines() if x.strip()]
        for key,_ in PHASES: self.store.update_phase(task_id,key,"pending")
        previous=None
        for i,row in enumerate(rows):
            phase={"review":"tests","items":"specs"}.get(row.get("stage"),row.get("stage"))
            if phase not in dict(PHASES): continue
            if previous and previous!=phase:
                self.store.update_phase(task_id,previous,"done",detail="Recorded phase complete")
                gate=self.store.wait_at_gate(task_id,previous)
                if gate:
                    self.store.add_event(task_id,"gate","Simulation paused at closed gate",phase_key=previous,payload={"gate_key":gate["gate_key"]})
                    while self.store.task(task_id)["state"]=="waiting_gate": await asyncio.sleep(.05)
                    decided=next(g for g in self.store.task(task_id)["gates"] if g["gate_key"]==gate["gate_key"])
                    if decided["state"]=="redo":
                        self.store.prepare_rerun(task_id, previous, "archive", actor="local user")
                        self.store.add_event(task_id,"steer",f"Redo steer: {decided['steer']}",phase_key=previous,payload={"provenance":"interactive V gate"})
            self.store.update_phase(task_id,phase,"running",detail=f"Recorded {row.get('provider')}/{row.get('model')} call {i+1}/{len(rows)}",completed=i+1,total=len(rows))
            self.store.add_event(task_id,"progress",f"[{phase}] {row.get('prompt_tokens',0)} input + {row.get('completion_tokens',0)} output tokens",phase_key=phase,payload={"simulated":True,"recorded_ts":row.get("ts")})
            if speed:
                now=datetime.fromisoformat(row["ts"].replace("Z","+00:00"))
                if previous is not None and i and rows[i-1].get("ts"):
                    before=datetime.fromisoformat(rows[i-1]["ts"].replace("Z","+00:00")); await asyncio.sleep(max(0,min(1,(now-before).total_seconds()/speed)))
            previous=phase
        if previous: self.store.update_phase(task_id,previous,"done",detail="Replay complete")
        source=Path(task["source_dir"])
        for phase,name in ARTIFACTS.items():
            path=source/name
            if path.exists():
                self.store.update_phase(task_id,phase,"done",detail="Imported recorded artifact")
                self.store.add_task_artifact(task_id,phase,name,str(path))
        self.store.audit("local user","simulation.completed",task_id,{"speed":"instant" if speed is None else speed})
        self.store.add_event(task_id,"complete","Simulation replay complete - no process was spawned",phase_key=previous)
    def start(self,task_id:str,speed:float|None):
        if task_id in self.running and not self.running[task_id].done(): raise ValueError("simulation already running")
        job=asyncio.create_task(self.replay(task_id,speed)); self.running[task_id]=job
