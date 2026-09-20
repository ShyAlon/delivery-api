"""Code-owned executable plan for the V-model. UI observes events; it does not wire stages."""
from __future__ import annotations
import asyncio,json,os,sys
from dataclasses import dataclass
from pathlib import Path
from vmodel_contracts.runtime import artifacts_root
from .phases import PHASE_BY_KEY, successor

@dataclass
class PhaseOrchestrator:
    store: object
    runner: object

    def _session(self,task_id:str)->str:
        import re
        for event in reversed(self.store.events(task_id)):
            m=re.search(r"(?:^|\s)session:\s*([A-Za-z0-9._-]+)",event.get("message",""))
            if m:return m.group(1)
        raise RuntimeError("completed interview did not report its durable session")

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
        await self.launch(task_id,nxt)

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
            await self.launch(task_id,"architecture")
        else:
            self.store.update_phase(task_id,"architecture","awaiting_approval",detail="Existing-system baseline required before architecture")
            self.store.add_event(task_id,"gate","Architecture paused for an existing SystemBaseline file",phase_key="architecture",payload={"cause":"Existing-system architecture needs repository facts.","next":"Import or place system-baseline.json in the session spec folder, then continue architecture."})

    async def launch(self,task_id:str,phase:str,*,redo:bool=False)->None:
        from .observability import event as activity
        activity("orchestrator.launch","Preparing phase subprocess",task_id=task_id,phase=phase,redo=redo)
        sid,_,interview,spec=self._paths(task_id);spec.mkdir(parents=True,exist_ok=True)
        baseline=spec/"system-baseline.json"
        if not baseline.exists():
            intent=json.loads((interview/"intent_model.json").read_text())
            baseline.write_text(json.dumps({"contract_version":intent["contract_version"],"baseline_version":"1.0","baseline_id":"pending-founder-choice","system_name":"Pending architecture baseline choice","mode":"greenfield","repositories":[],"immutable_decisions":[]},indent=2)+"\n")
        built=os.environ.get("REQUI_RE_DIST")=="1"
        repo=(Path.cwd() if built else Path(os.environ.get("SPEC_GENERATOR_DIR",str(Path(__file__).resolve().parents[3]/"spec-generator"))).resolve())
        python=Path(sys.executable) if built else (repo/".venv/bin/python")
        if not python.exists():python=Path(sys.executable)
        cmd=[str(python),"-m","spec_generator.cli",str(interview/"intent_model.json"),"--conops",str(interview/"conops.json"),"--system-baseline",str(baseline),"--out",str(spec),"--to",PHASE_BY_KEY[phase].generator_stage]
        if redo:cmd.extend(["--from",PHASE_BY_KEY[phase].generator_stage])
        env={"PYTHONUNBUFFERED":"1",**({} if built else {"PYTHONPATH":str(repo/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})}
        code=await self.runner.run(task_id,cmd,cwd=repo,phase=phase,env=env,infer_phase=False)
        activity("orchestrator.exit","Phase subprocess exited",task_id=task_id,phase=phase,exit_code=code)
