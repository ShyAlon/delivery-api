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
 source=Path(__import__('os').environ['DELIVERY_TEST_SPEC_SOURCE']).parent
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


def test_private_real_full_graph_gates_persist_complete_bundle(tmp_path,monkeypatch):
 gp=configure_graph_models(monkeypatch)
 scope=SimpleNamespace(owner_id='00000000-0000-0000-0000-000000000001');cap=SimpleNamespace(task_id='fixture-task',session_id='s',generation=1,scope=scope,validate=lambda s:None)
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
 with cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog('s')):
  runner.generate(cap,root)
 assert decisions==['srs','architecture']
 from artifact_receipts import inventory
 files,documents=inventory(root)
 assert len(files)>=11
 for name in ('needs.json','srs.json','architecture.json','items.json','tests.json','validation.json','manifest.json','traceability.json','AGENTS.md'):
  assert (root/'spec'/name).is_file(),name
 assert {'needs','srs','architecture','specs','tests','validation'} <= {p for p,s in phases if s=='done'}


def test_exact_generator_checkpoint_resume_preserves_srs_and_only_new_gate(tmp_path,monkeypatch):
 gp=configure_graph_models(monkeypatch)
 owner='00000000-0000-0000-0000-000000000001';cap=SimpleNamespace(task_id='fixture',session_id='s',scope=SimpleNamespace(owner_id=owner),validate=lambda s:None)
 root=tmp_path/'tenants'/owner/'sessions'/'s';(root/'interview').mkdir(parents=True)
 (root/'interview/intent_model.json').write_text(json.dumps(gp.INTENT))
 raw=json.loads((ROOT/'tests/fixtures/revision-source.json').read_text())['conops'];raw['session_id']='s';(root/'interview/conops.json').write_text(json.dumps(raw))
 phases=[];store=SimpleNamespace(update_phase=lambda *a:phases.append(a),task=lambda tid:{'phases':[{'key':'srs','state':'approved'}]})
 decisions=[]
 def gate(review,digest):decisions.append(review['gate']);return {'review_sha256':digest,'decision':{'action':'accept'}}
 runner=PrivatePipeline(store=store,artifacts_base=tmp_path,model='fixture',gateway_factory=lambda *a:None,generator_clients=lambda **kw:None,interview_clients=lambda **kw:None,answer_input=lambda x:None,review_gate=gate,progress=lambda *a:None)
 from spec_generator import graph as gm
 real=gm._call_json
 def fail(stage,*a,**kw):
  if stage=='architecture':raise RuntimeError('fixture interruption')
  return real(stage,*a,**kw)
 monkeypatch.setattr(gm,'_call_json',fail)
 from spec_generator.stop_fence import StopFence
 from spec_generator.cooperative_run import cli_execution
 from spec_generator.telemetry import UsageLog
 with cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog('s')):
  with pytest.raises(RuntimeError):runner.generate(cap,root)
 assert decisions==['srs']
 before={n:(root/'spec'/n).read_bytes() for n in ('needs.json','srs.json')}
 from recovery_generator import generator_review
 proof=generator_review(root,'s');assert proof['architecture_stage_reexecution_possible']
 monkeypatch.setattr(gm,'_call_json',real)
 with cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog('s')):runner.generate(cap,root,generator_resume=proof)
 assert decisions==['srs','architecture']
 assert all((root/'spec'/n).read_bytes()==v for n,v in before.items())
