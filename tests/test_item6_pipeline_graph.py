"""Real full generator graph with fixture models, no network/paid dispatch."""
import importlib.util,sys,json
from pathlib import Path
from types import SimpleNamespace
import pytest
pytestmark=pytest.mark.producer_integration
ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'scripts/item6'))
from pipeline_executor import PrivatePipeline


def configure_graph_models(monkeypatch):
 source=Path(__import__('os').environ['DELIVERY_TEST_SPEC_SOURCE'])
 if source.name=='src':source=source.parent
 if not (source/'tests/conftest.py').is_file():raise RuntimeError('DELIVERY_TEST_SPEC_SOURCE must name spec-generator checkout or its src directory')
 sys.path.insert(0,str(source/'tests'))
 # Own fixture import explicitly to avoid delivery tests' conftest shadowing.
 cs=importlib.util.spec_from_file_location('generator_fixtures',source/'tests/conftest.py');cf=importlib.util.module_from_spec(cs);cs.loader.exec_module(cf)
 old=sys.modules.get('conftest');sys.modules['conftest']=cf
 try:
  ps=importlib.util.spec_from_file_location('test_per_item_resume',source/'tests/test_per_item_resume.py');pm=importlib.util.module_from_spec(ps);sys.modules['test_per_item_resume']=pm;ps.loader.exec_module(pm)
  ts=importlib.util.spec_from_file_location('generator_progress_fixture',source/'tests/test_progress.py');gp=importlib.util.module_from_spec(ts);ts.loader.exec_module(gp)
 finally:
  if old:sys.modules['conftest']=old
  else:sys.modules.pop('conftest',None)
 from spec_generator import model_config as mc
 monkeypatch.setenv('SPEC_GENERATOR_BULK','1');monkeypatch.setattr(mc,'_DOTENV',{});monkeypatch.setattr(mc,'_CALLS_USED',0)
 def clear_refs(body):
  if isinstance(body,dict):return {k:([] if k=='baseline_refs' else clear_refs(v)) for k,v in body.items()}
  if isinstance(body,list):return [clear_refs(v) for v in body]
  return body
 class GreenfieldArchitecture(cf.SchemaRoutingArchitectureModel):
  def stream(self,messages):
   from langchain_core.messages import AIMessage
   self.calls+=1
   body=clear_refs(pm._arch_answer(self.schema))
   prompt='\n'.join(str(m.content) for m in messages)
   if isinstance(body,dict) and 'executes_concept_ids' in body and 'concept:native-app' in prompt:
    body['executes_concept_ids']=['concept:native-app']
   yield AIMessage(content=json.dumps(body))
 def factory(stage,**kw):
  if stage=='architecture':return GreenfieldArchitecture(stage,kw.get('schema') or {})
  return cf.schema_routing_factory(stage,kw.get('schema'),lambda:gp.FakeModel(stage))
 monkeypatch.setattr(mc,'build_chat_model',factory)
 return gp


def test_private_real_full_graph_gates_persist_complete_bundle(tmp_path,monkeypatch,pg_pipeline):
 gp=configure_graph_models(monkeypatch)
 workspace,pg_cap,job=pg_pipeline;scope=pg_cap.scope
 cap=SimpleNamespace(task_id=pg_cap.task_id,session_id=pg_cap.session_id,generation=pg_cap.generation,scope=scope,validate=lambda store:pg_cap.validate(workspace.store))
 root=tmp_path/'tenants'/scope.owner_id/'sessions'/'s';(root/'interview').mkdir(parents=True)
 from vmodel_contracts import IntentModel
 intent=IntentModel.model_validate(gp.INTENT).model_dump(mode='json')
 (root/'interview/intent_model.json').write_text(json.dumps(intent))
 # Current typed ConOps fixture, with session rewritten to this exact graph.
 raw=json.loads((ROOT/'tests/fixtures/revision-source.json').read_text())['conops'];raw['session_id']='s'
 (root/'interview/conops.json').write_text(json.dumps(raw))
 phases=[];store=SimpleNamespace(update_phase=lambda tid,phase,state:phases.append((phase,state)))
 decisions=[]
 def gate(review,digest):
  decisions.append(review['gate']);return {'review_sha256':digest,'decision':{'action':'accept'}}
 runner=PrivatePipeline(store=store,artifacts_base=tmp_path,model='fixture',gateway_factory=lambda *a:None,generator_clients=lambda **kw:None,
  interview_clients=lambda **kw:None,answer_input=lambda x:None,review_gate=gate,progress=lambda *a:None)
 from spec_generator.stop_fence import StopFence
 from spec_generator.cooperative_run import cli_execution
 from spec_generator.telemetry import UsageLog
 from spec_generator.worker_runtime import JobRuntime,job_runtime
 from spec_generator.model_config import StageModel
 from delivery_api.generator_checkpoint import GeneratorCheckpoint
 runtime=JobRuntime({stage:StageModel('ollama','fixture') for stage in ('needs','srs','architecture','items','specs','tests','validation','review','srs_review','architecture_review')},str(root/'calls.jsonl'),lambda text:None,settings={'SPEC_GENERATOR_BULK':'1'},checkpoint_factory=lambda ns:GeneratorCheckpoint(workspace.store,pg_cap,job,ns))
 runtime.bind_worker(owner_id=scope.owner_id,task_id=cap.task_id,job_id=job,generation=1,session_root=root)
 from requi_child_contract.compute import measured_work
 from delivery_api.child_compute_events import ChildComputeEvents
 class Events:
  def __init__(self):self.events=[]
  def emit(self,kind,wait_id=None):
   event={'kind':kind,'sequence':len(self.events)+1,'monotonic_ns':(len(self.events)+1)*1000000}
   if wait_id is not None:event['wait_id']=wait_id
   self.events.append(event)
 events=Events()
 with measured_work(events),job_runtime(runtime),cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog('s')):
  runner.generate(cap,root)
 assert decisions==['srs','architecture']
 assert [e['kind'] for e in events.events]==['start','wait.begin','wait.end','wait.begin','wait.end','finish']
 assert events.events[1]['wait_id']==events.events[2]['wait_id']
 assert events.events[3]['wait_id']==events.events[4]['wait_id']
 assert events.events[1]['wait_id']!=events.events[3]['wait_id']
 accounting=ChildComputeEvents()
 for event in events.events:accounting.accept(event)
 assert accounting.milliseconds()==3  # Five elapsed ms, two excluded attended waits.

 from artifact_receipts import inventory
 files,documents=inventory(root)
 assert len(files)>=11
 for name in ('needs.json','srs.json','architecture.json','items.json','tests.json','validation.json','manifest.json','traceability.json','AGENTS.md'):
  assert (root/'spec'/name).is_file(),name
 assert {'needs','srs','architecture','specs','tests','validation'} <= {p for p,s in phases if s=='done'}


@pytest.fixture
def pg_pipeline(pg_workspace_factory):
 from delivery_api.tenancy import tenant_context
 with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
  store=workspace.store;tid=store.create_task('Pipeline PG fixture')['task_id']
  store.set_engine_session(tid,'s');job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
  try:yield workspace,cap,job
  finally:store.finish_worker(job)
