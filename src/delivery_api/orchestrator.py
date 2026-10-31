"""Code-owned executable plan for the V-model. UI observes events; it does not wire stages."""
from __future__ import annotations
import json,os,sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from vmodel_contracts.runtime import artifacts_root
from . import model_routes, config
from .tenant_files import authorized_session
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
        bound=self.store.engine_session(task_id)
        if bound:return bound
        if config.CLOUD_TENANTS:raise RuntimeError("authorized session binding required")
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
        sid=self._session(task_id);root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),sid,self.store)
        return sid,root,root/"interview",root/"spec"

    async def continue_after(self,task_id:str,phase:str,*,model_route:str|None=None)->None:
        next_definition=successor(phase)
        if next_definition is None:return
        nxt=next_definition.key
        from .observability import event as activity
        activity("orchestrator.continue","Launching next V-model phase",task_id=task_id,completed_phase=phase,next_phase=nxt)
        await self.execute_phase(task_id,nxt,model_route=model_route)

    def _baseline_path(self,task_id:str)->Path:
        sid,_,interview,spec=self._paths(task_id);return spec/"system-baseline.json"
    def _baseline_exists(self,task_id:str)->bool:
        p=self._baseline_path(task_id)
        if hasattr(self.store,'_postgres'):
            try:return json.loads(self.store.read_artifact_bytes(task_id,'baseline','system-baseline.json')).get('baseline_id') not in (None,'pending-founder-choice')
            except KeyError:return False
        if not p.exists():return False
        try:return json.loads(p.read_text()).get("baseline_id") not in (None,"pending-founder-choice")
        except Exception:return False

    def _ensure_greenfield(self,task_id:str)->Path:
        path=self._baseline_path(task_id)
        if hasattr(self.store,'_postgres'):
            from .artifact_publication import publish_preserving_revision
            with self.store._task_operation_guard('task.rerun'):
                self.store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
                try:existing=json.loads(self.store.read_artifact_bytes(task_id,'baseline','system-baseline.json'))
                except KeyError:existing=None
                if existing and existing.get('baseline_id')!='pending-founder-choice':
                    if existing.get('mode')!='greenfield':raise ValueError('Existing-system baseline is outside the current greenfield flow; it was not overwritten')
                    return path
                task=self.store.task(task_id);intent=json.loads(self.store.read_artifact_bytes(task_id,'intent','intent_model.json'))
                body={'contract_version':intent['contract_version'],'baseline_version':'1.0','baseline_id':f'{self._session(task_id)}-greenfield-v1',
                    'system_name':task['title'],'mode':'greenfield','repositories':[],'immutable_decisions':[]}
                from vmodel_contracts.baseline import SystemBaseline
                SystemBaseline.model_validate(body)
                publish_preserving_revision(self.store,task_id,{('baseline','system-baseline.json'):json.dumps(body,indent=2).encode()+b'\n'})
                self.store.add_event(task_id,'baseline','Greenfield assumed for the current product flow',phase_key='architecture',payload={'mode':'greenfield','source':'current product policy','storage':'postgres'})
            return path
        if path.exists():
            existing=json.loads(path.read_text())
            if existing.get('baseline_id')!='pending-founder-choice':
                if existing.get('mode')!='greenfield':raise ValueError('Existing-system baseline is outside the current greenfield flow; it was not overwritten')
                return path
        task=self.store.task(task_id);_,_,interview,_=self._paths(task_id)
        intent=json.loads((interview/'intent_model.json').read_text())
        body={'contract_version':intent['contract_version'],'baseline_version':'1.0',
              'baseline_id':f'{self._session(task_id)}-greenfield-v1','system_name':task['title'],
              'mode':'greenfield','repositories':[],'immutable_decisions':[]}
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(body,indent=2)+'\n')
        self.store.add_event(task_id,'baseline','Greenfield assumed for the current product flow',phase_key='architecture',payload={'mode':'greenfield','path':str(path),'source':'current product policy'})
        return path

    async def resolve_baseline_requirement(self,task_id:str,action:str)->None:
        """Resolve the configured baseline prerequisite, then continue uniformly."""
        from vmodel_contracts.baseline import SystemBaseline
        if hasattr(self.store,'_postgres'):
            path=self._baseline_path(task_id)
            if action=='greenfield':self._ensure_greenfield(task_id)
            elif action=='validate_file':
                try:raw=self.store.read_artifact_bytes(task_id,'baseline','system-baseline.json')
                except KeyError:raise ValueError('SystemBaseline is missing from the owned PG manifest')
                SystemBaseline.model_validate_json(raw)
            else:raise ValueError('unknown baseline requirement action')
            self.runner.active_work.pop(task_id,None)
            self.store.add_event(task_id,'baseline','Architecture baseline requirement resolved',phase_key='architecture',payload={'mode':action,'storage':'postgres','resolved':True})
            await self.execute_phase(task_id,'architecture')
            return
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
        sid,_,interview,spec=self._paths(task_id)
        if not hasattr(self.store,'_postgres'):spec.mkdir(parents=True,exist_ok=True)
        baseline=self._ensure_greenfield(task_id)
        built=os.environ.get("REQUI_RE_DIST")=="1"
        repo=(Path.cwd() if built else Path(os.environ.get("SPEC_GENERATOR_DIR",str(Path(__file__).resolve().parents[3]/"spec-generator"))).resolve())
        python=Path(sys.executable) if built else (repo/".venv/bin/python")
        if not python.is_file() and not built:
            raise RuntimeError(f"spec-generator environment is missing at {python}. Run scripts/pull-all.sh before retrying the phase.")
        cmd=[str(python),"-m","spec_generator.cli",str(interview/"intent_model.json"),"--conops",str(interview/"conops.json"),"--system-baseline",str(baseline),"--out",str(spec),"--to",definition.generator_stage]
        if phase=="validation":cmd.append("--final-review")
        if mode is ExecutionMode.REDO:cmd.extend(["--from",definition.generator_stage])
        # Attended run: items may not start while an architecture slot lacks a founder decision.
        if definition.generator_stage=="items":cmd.append("--require-decisions")
        stored,source=(model_route,"phase selection") if model_route else model_routes.task_default_model(self.store,task_id,definition.generator_stage)
        route,sep,model=stored.partition(":")
        if source != "legacy generator configuration (no recorded exact selection)" and not sep:
            raise ValueError("exact model selection must include a provider and model")
        if route not in ("ollama","deepseek"):raise ValueError(f"unsupported model route {stored!r}")
        route_value=model_routes.route_value(route,model if sep else None)
        env={"PYTHONUNBUFFERED":"1","VMODEL_PROGRESS_TASK_ID":task_id,"VMODEL_PROGRESS_SESSION_ID":sid,f"SPEC_GENERATOR_MODEL_{definition.generator_stage.upper()}":route_value,**({} if built else {"PYTHONPATH":str(repo/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})}
        if model_route:
            self.store.set_model_default(task_id,route_value)
        self.store.add_event(task_id,"model_route",f"{phase} uses {route_value} ({source})",phase_key=phase,payload={"route":route,"resolved":route_value,"provider":route,"model":route_value.split(":",1)[1],"mode":mode.value,"source":source})
        if config.CLOUD_TENANTS:
            from .worker_capability import WorkerCapability
            capability=WorkerCapability.capture(self.store,task_id)
            capability.validate(self.store)
            # Producer progress and auxiliary files must share the same private
            # root as the canonical phase output, not the legacy shared root.
            env['VMODEL_ARTIFACTS_DIR']=str(spec.parent.parent)
            env['VMODEL_TENANT_OWNER']=capability.scope.owner_id
            env['VMODEL_PROGRESS_GENERATION']=str(capability.generation)
            if config.ARTIFACT_RESTORE and not hasattr(self.store,'_postgres'):
                # The child reads completed predecessors straight from disk; on a
                # second host they must be restored before the subprocess starts.
                from . import artifact_restore
                artifact_restore.restore_completed_phases(self.store,task_id,spec.parent)
        code=await self.runner.run(task_id,cmd,cwd=repo,phase=phase,env=env,infer_phase=False)
        activity("orchestrator.exit","Phase subprocess exited",task_id=task_id,phase=phase,exit_code=code)

    async def launch(self,task_id:str,phase:str,*,redo:bool=False)->None:
        """Compatibility adapter; new callers use execute_phase with a mode."""
        await self.execute_phase(task_id,phase,mode=ExecutionMode.REDO if redo else ExecutionMode.START)
