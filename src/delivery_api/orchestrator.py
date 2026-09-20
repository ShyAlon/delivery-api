"""Code-owned executable plan for the V-model. UI observes events; it does not wire stages."""
from __future__ import annotations
import asyncio,json,os,sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from vmodel_contracts.runtime import artifacts_root
from .phases import PHASE_BY_KEY, successor

class ExecutionMode(str,Enum):
    START="start"
    RESUME="resume"
    REDO="redo"

@dataclass
class PhaseOrchestrator:
    store: object
    runner: object

    def _session(self,task_id:str)->str:
        import re
        for event in reversed(self.store.events(task_id)):
            m=re.search(r"(?:^|\s)session:\s*([A-Za-z0-9._-]+)",event.get("message",""))
            if m:return m.group(1)
            # Long-lived tasks may have rotated the original session event out
            # of the bounded snapshot. Requirements carry the canonical
            # artifacts path, which is durable evidence of the same session.
            path=str((event.get("payload") or {}).get("path") or "")
            m=re.search(r"(?:^|[/\\])artifacts[/\\]([A-Za-z0-9._-]+)(?:[/\\]|$)",path)
            if m:return m.group(1)
        raise RuntimeError("completed interview did not report its durable session or artifacts path")

    def _paths(self,task_id:str):
        sid=self._session(task_id);root=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")) / sid
        return sid,root,root/"interview",root/"spec"

    async def continue_after(self,task_id:str,phase:str)->None:
        next_definition=successor(phase)
        if next_definition is None:return
        nxt=next_definition.key
        from .observability import event as activity
        activity("orchestrator.continue","Launching next V-model phase",task_id=task_id,completed_phase=phase,next_phase=nxt)
        if next_definition.prerequisite=="system-baseline" and not self._baseline_exists(task_id):
            await self._choose_baseline(task_id);return
        await self.execute_phase(task_id,nxt)

    def _baseline_path(self,task_id:str)->Path:
        sid,_,interview,spec=self._paths(task_id);return spec/"system-baseline.json"
    def _baseline_exists(self,task_id:str)->bool:
        p=self._baseline_path(task_id)
        if not p.exists():return False
        try:return json.loads(p.read_text()).get("baseline_id") not in (None,"pending-founder-choice")
        except Exception:return False

    async def _choose_baseline(self,task_id:str)->None:
        decision={"type":"founder_decision","decision_id":"system-baseline","title":"Choose the architecture baseline","prompt":"Should architecture treat this as a greenfield product or use an existing-system baseline?","options":[{"value":"greenfield","label":"Greenfield","summary":"No existing repositories constrain the architecture."},{"value":"existing","label":"Existing system","summary":"Pause architecture until a SystemBaseline file is supplied."}],"rationale_required":False,"answer_format":"<value>"}
        self.store.update_phase(task_id,"architecture","running",detail="Waiting for architecture baseline")
        answer=await self.runner.request_decision(task_id,"architecture",decision)
        value=answer.strip().split()[0].lower()
        if value.startswith("greenfield"):
            task=self.store.task(task_id);p=self._baseline_path(task_id);p.parent.mkdir(parents=True,exist_ok=True)
            # Preserve the producing contract version from the intent artifact.
            _,_,interview,_=self._paths(task_id);intent=json.loads((interview/"intent_model.json").read_text())
            body={"contract_version":intent["contract_version"],"baseline_version":"1.0","baseline_id":f"{self._session(task_id)}-greenfield-v1","system_name":task["title"],"mode":"greenfield","repositories":[],"immutable_decisions":[]}
            p.write_text(json.dumps(body,indent=2)+"\n")
            self.store.add_event(task_id,"baseline","Greenfield architecture baseline confirmed",phase_key="architecture",payload={"mode":"greenfield","path":str(p)})
            await self.execute_phase(task_id,"architecture")
        else:
            path=self._baseline_path(task_id);path.parent.mkdir(parents=True,exist_ok=True)
            requirement={"kind":"external_input","title":"Existing-system baseline required","summary":"Architecture is paused until the real system facts are supplied.","path":str(path),"format":"SystemBaseline JSON","required_fields":["baseline_id","system_name","mode: existing","repositories (at least one)"],"next":"Save a valid system-baseline.json at this path, then restart Requi.re; recovery will launch Architecture."}
            self.store.update_phase(task_id,"architecture","awaiting_approval",detail=f"SystemBaseline JSON required at {path}")
            self.runner.pause_for_requirement(task_id,"architecture",requirement)
            self.store.add_event(task_id,"requirement","Architecture paused for an existing SystemBaseline file",phase_key="architecture",payload=requirement)

    async def resolve_baseline_requirement(self,task_id:str,action:str)->None:
        """Resolve the configured baseline prerequisite, then continue uniformly."""
        from vmodel_contracts.baseline import SystemBaseline
        path=self._baseline_path(task_id);path.parent.mkdir(parents=True,exist_ok=True)
        if action=="greenfield":
            task=self.store.task(task_id);_,_,interview,_=self._paths(task_id);intent=json.loads((interview/"intent_model.json").read_text())
            body={"contract_version":intent["contract_version"],"baseline_version":"1.0","baseline_id":f"{self._session(task_id)}-greenfield-v1","system_name":task["title"],"mode":"greenfield","repositories":[],"immutable_decisions":[]}
            path.write_text(json.dumps(body,indent=2)+"\n")
        elif action=="validate_file":
            if not path.is_file():raise ValueError(f"SystemBaseline file was not found at {path}")
            SystemBaseline.model_validate_json(path.read_text())
        else:raise ValueError("unknown baseline requirement action")
        self.runner.active_work.pop(task_id,None)
        self.store.add_event(task_id,"baseline","Architecture baseline requirement resolved",phase_key="architecture",payload={"mode":action,"path":str(path),"resolved":True})
        await self.execute_phase(task_id,"architecture")

    async def execute_phase(self,task_id:str,phase:str,*,mode:ExecutionMode=ExecutionMode.START,model_route:str|None=None)->None:
        """Execute any configured generator phase through one lifecycle."""
        from .observability import event as activity
        definition=PHASE_BY_KEY[phase]
        if definition.executor!="generator":raise ValueError(f"{phase} is owned by {definition.executor}, not generator")
        activity("orchestrator.launch","Preparing phase subprocess",task_id=task_id,phase=phase,mode=mode.value)
        sid,_,interview,spec=self._paths(task_id);spec.mkdir(parents=True,exist_ok=True)
        baseline=spec/"system-baseline.json"
        if not baseline.exists():
            intent=json.loads((interview/"intent_model.json").read_text())
            baseline.write_text(json.dumps({"contract_version":intent["contract_version"],"baseline_version":"1.0","baseline_id":"pending-founder-choice","system_name":"Pending architecture baseline choice","mode":"greenfield","repositories":[],"immutable_decisions":[]},indent=2)+"\n")
        built=os.environ.get("REQUI_RE_DIST")=="1"
        repo=(Path.cwd() if built else Path(os.environ.get("SPEC_GENERATOR_DIR",str(Path(__file__).resolve().parents[3]/"spec-generator"))).resolve())
        python=Path(sys.executable) if built else (repo/".venv/bin/python")
        if not python.exists():python=Path(sys.executable)
        cmd=[str(python),"-m","spec_generator.cli",str(interview/"intent_model.json"),"--conops",str(interview/"conops.json"),"--system-baseline",str(baseline),"--out",str(spec),"--to",definition.generator_stage]
        if mode is ExecutionMode.REDO:cmd.extend(["--from",definition.generator_stage])
        stored=model_route or next(((event.get("payload") or {}).get("route") for event in reversed(self.store.events(task_id)) if event.get("kind")=="model_route" and event.get("phase_key")==phase),None) or (self.store.task(task_id) or {}).get("model_route") or "ollama"
        route="deepseek" if stored.startswith("deepseek") else "ollama" if stored.startswith("ollama") else stored
        if route not in ("ollama","deepseek"):raise ValueError(f"unsupported model route {stored!r}")
        route_value="ollama:qwen2.5-coder:14b" if route=="ollama" else "deepseek:deepseek-chat"
        env={"PYTHONUNBUFFERED":"1",f"SPEC_GENERATOR_MODEL_{definition.generator_stage.upper()}":route_value,**({} if built else {"PYTHONPATH":str(repo/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})}
        self.store.add_event(task_id,"model_route",f"{phase} uses {route}",phase_key=phase,payload={"route":route,"provider":route,"model":route_value.split(":",1)[1],"mode":mode.value})
        code=await self.runner.run(task_id,cmd,cwd=repo,phase=phase,env=env,infer_phase=False)
        activity("orchestrator.exit","Phase subprocess exited",task_id=task_id,phase=phase,exit_code=code)

    async def launch(self,task_id:str,phase:str,*,redo:bool=False)->None:
        """Compatibility adapter; new callers use execute_phase with a mode."""
        await self.execute_phase(task_id,phase,mode=ExecutionMode.REDO if redo else ExecutionMode.START)
