"""Restricted PG jobs ->real generator graph ->private revision; fixture models only."""
import os, uuid
from types import SimpleNamespace
import pytest
pytest.importorskip("interview_engine.application")
pytest.importorskip("spec_generator")
from test_item6_interview import configure_interview_models
from test_item6_pipeline_graph import configure_graph_models,PrivatePipeline
from test_item6_isolation import config
from delivery_api.tenancy import tenant_context, session_root
from delivery_api.postgres_store import PostgresStore
from revision_executor import PrivateGateEdit
ADMIN=os.getenv('ITEM6_TEST_ADMIN_DSN')
pytestmark=pytest.mark.skipif(not ADMIN,reason='explicit isolated item6 PostgreSQL required')


def test_connected_real_graph_and_closed_revision_with_retained_approval(tmp_path,monkeypatch):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 from vmodel_contracts import IntentModel
 gp=configure_graph_models(monkeypatch)
 monkeypatch.setenv('DEEPSEEK_API_KEY','hermetic-fixture-not-a-key')
 interview_models=configure_interview_models(monkeypatch)
 sid='fixture-'+uuid.uuid4().hex
 from spec_generator import model_config as mc,telemetry
 monkeypatch.setattr(telemetry,'price_for',lambda *a:(0,0))  # hermetic model, not a live rate
 from spec_generator.wire_binding import register_model,_binding
 from delivery_api.producer_admission import WorkerAdmissionGateway
 original_factory=mc.build_chat_model
 def fixture_model(stage,**kw):
  model=original_factory(stage,**kw);stream=model.stream
  def paid_fixture_stream(messages):
   gateway=_binding.get()[0]
   quote={'provider':'deepseek','model':'deepseek-chat','price_version':'hermetic','bound_version':'deepseek-chat',
     'input_max':100000,'output_max':100000,'expense_input':0,'expense_output':0,'retail_input':1,'retail_output':1,'retail_units_per_credit':1000}
   attempt=gateway.store.reserve_provider_attempt(gateway.job_id,uuid.uuid4().hex,quote,'fixture-'+uuid.uuid4().hex,require_prepaid=True)['attempt_id']
   gateway.start(attempt)
   runtime=__import__('spec_generator.worker_runtime',fromlist=['current_runtime']).current_runtime()
   current_sid=runtime.worker_binding[4].split('/')[-1]
   if current_sid.startswith('revised-'):current_sid=sid
   for chunk in stream(messages):
    chunk.content=chunk.content.replace('\"session_id\": \"s\"','\"session_id\": \"'+current_sid+'\"').replace('\"intent_model_ref\": \"s\"','\"intent_model_ref\": \"'+current_sid+'\"')
    yield chunk
   gateway.store.transition_provider_attempt(attempt,'settle',{'input_tokens':1,'output_tokens':1})
  model.stream=paid_fixture_stream
  return register_model(model,stage=stage,provider='deepseek',model='deepseek-chat')
 monkeypatch.setattr(mc,'build_chat_model',fixture_model)
 from interview_engine import model_config as imc,telemetry as itelemetry
 from interview_engine.wire_binding import register_model as register_interview,_binding as interview_binding
 monkeypatch.setattr(itelemetry,'price_for',lambda *a:(0,0))
 for stage,model in interview_models.items():
  invoke=model.invoke
  def paid_invoke(messages,invoke=invoke):
   gateway=interview_binding.get()[0]
   quote={'provider':'deepseek','model':'deepseek-chat','price_version':'hermetic','bound_version':'fixture',
     'input_max':100000,'output_max':100000,'expense_input':0,'expense_output':0,'retail_input':1,'retail_output':1,'retail_units_per_credit':1000}
   attempt=gateway.store.reserve_provider_attempt(gateway.job_id,uuid.uuid4().hex,quote,'fixture-'+uuid.uuid4().hex,require_prepaid=True)['attempt_id']
   gateway.start(attempt);result=invoke(messages)
   gateway.store.transition_provider_attempt(attempt,'settle',{'input_tokens':1,'output_tokens':1});return result
  model.invoke=paid_invoke
 def interview_factory(stage,**kw):return register_interview(interview_models[stage],stage=stage,provider='deepseek',model='deepseek-chat')
 monkeypatch.setattr(imc,'build_chat_model',interview_factory)
 monkeypatch.setattr(__import__('vmodel_contracts'),'price_for',lambda *a:(0,0))
 info=conninfo_to_dict(ADMIN);assert info['dbname'].startswith('requi_item6_') and info['host']=='/tmp'
 cfg=__import__('dataclasses').replace(config(tmp_path),run_id=uuid.uuid4().hex);cfg.initialize()
 from local_auth import DisposableSignup
 from private_adapters import IdentityAdapter,OperatorAdapter
 from delivery_api.operator_authorization import OperatorPrincipal
 from test_item6_connected_pg import hermetic_purchase
 signup=DisposableSignup(admin_connection=lambda:psycopg.connect(ADMIN),mode='hermetic')
 store=PostgresStore(make_conninfo(**(info|{'user':'delivery_app'})))
 identity=IdentityAdapter(signup=signup,verifier=signup.verifier,store=store)
 receipt=identity(cfg,{});scope=identity.scope
 with psycopg.connect(ADMIN) as c:
  c.execute('INSERT INTO admission_limits(owner_id,config_version,period_seconds,rate_window_seconds,concurrency_max) VALUES(%s,%s,86400,60,3)',(scope.owner_id,'hermetic'))
  c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000,0)',(scope.owner_id,))
  c.execute('UPDATE operator_principals SET enabled=false WHERE enabled')
  c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'item6-hermetic',ARRAY['payments.read'],true)",(scope.owner_id,))
 principal=OperatorPrincipal(scope.owner_id,'item6-hermetic',frozenset({'payments.read'}))
 operator=OperatorAdapter(identity,store=store,read_reviewed_registry=lambda:principal)(cfg,{'identity':receipt})
 purchased=hermetic_purchase(cfg,identity,store,ADMIN,receipt,operator)
 assert purchased['balance_units']==12000
 runner=PrivatePipeline(store=store,artifacts_base=cfg.root,model='deepseek-chat',gateway_factory=lambda cap,job:WorkerAdmissionGateway(store,job),
  generator_clients=lambda **kw:None,interview_clients=lambda **kw:None,answer_input=lambda x:None,
  review_gate=lambda review,digest:{'review_sha256':digest,'decision':{'action':'accept'}},progress=lambda *a:None)
 real_worker=runner.worker
 def fixture_worker():
  worker=real_worker();runtime=worker.runtime_factory
  def fixture_runtime(cap,root):
   result=runtime(cap,root);result.settings['SPEC_GENERATOR_BULK']='1';return result
  worker.runtime_factory=fixture_runtime;return worker
 runner.worker=fixture_worker
 with tenant_context(scope):
  from vmodel_contracts.interactive import InputResponse,InputKind
  def answer(request):
   if request.kind==InputKind.CHOICE:return InputResponse(input_id=request.input_id,actor_id=scope.owner_id,value=1,rationale='reliable native APIs')
   if request.kind==InputKind.APPROVAL:return InputResponse(input_id=request.input_id,actor_id=scope.owner_id,value=1)
   raise ValueError('unexpected hermetic input')
  runner.answer=answer
  tid=runner(cfg,scope);sid=store.task(tid)['engine_session_id'];root=session_root(cfg.root,scope,sid)
  assert all(p['state'] in ('done','approved') for p in store.task(tid)['phases'])
  assert not (root/'interview/intent_model.json').exists() and not (root/'interview/checkpoints.sqlite').exists()
  assert not (root/'spec/srs.json').exists() and not (root/'spec/checkpoints.sqlite').exists(), 'canonical business data stays in PG'
  assert not list(root.rglob('*.jsonl')) and not list(root.rglob('*.raw.txt')), 'diagnostics stay in PG too'
  from artifact_receipts import PipelineReceiptAdapter
  from completion_adapters import EvidenceReader
  reader=EvidenceReader(store)
  proved_pipeline=PipelineReceiptAdapter(identity,store=store,artifacts_base=cfg.root,execute_pipeline=lambda *a:tid,read_completed_jobs=reader.jobs,read_usage=reader.usage)(cfg,{'identity':receipt,'operator':operator,'sandbox_purchase':purchased})
  adapter=PrivateGateEdit(SimpleNamespace(scope=scope),store=store,artifacts_base=cfg.root,pipeline=runner,
   choose_change=lambda review,digest:{'review_sha256':digest,'change_request':'Explain the task board offline behavior in purpose','extra_cost_acknowledged':True},
   accept_fresh_review=lambda review:{'review_sha256':review['review_sha256'],'choice':'approve','change_request':'','extra_cost_acknowledged':True})
  from artifact_receipts import inventory
  original_files,_=inventory(root,store=store,capability=__import__('delivery_api.worker_capability',fromlist=['WorkerCapability']).WorkerCapability.capture(store,tid))
  pipeline_receipt={'task_id':tid,'session_id':sid,'artifact_sha256':original_files}
  result=adapter(cfg,{'pipeline':pipeline_receipt})
  assert result['installed_lineage_verified'] and result['stale_rejected'] and result['source_preserved']

  from completion_adapters import EvidenceReader,FinalArtifactsAdapter,RecoveryAdapter
  identity=SimpleNamespace(scope=scope)
  previous={'pipeline':pipeline_receipt,'gate_edit':result}
  real_review=store.decision_review
  def corrupted_review(task_id,wait_id,revision=None):
   held=real_review(task_id,wait_id,revision)
   if held and wait_id.startswith('revision-next-review:'):return held|{'artifact_sha256':'0'*64}
   return held
  monkeypatch.setattr(store,'decision_review',corrupted_review)
  with pytest.raises(ValueError,match='approved revision artifact differs'):
   FinalArtifactsAdapter(identity,store=store,artifacts_base=cfg.root)(cfg,previous)
  assert not (cfg.root/'reviewed-artifacts.zip').exists(), 'bad evidence exports nothing'
  monkeypatch.setattr(store,'decision_review',real_review)
  exported=FinalArtifactsAdapter(identity,store=store,artifacts_base=cfg.root)(cfg,previous)
  assert exported['download_verified']
  recovered=RecoveryAdapter(identity,store=store,read_evidence=EvidenceReader(store))(cfg,previous)
  assert recovered['foreign_owner_denied'] and recovered['unknown_attempts']==0
  with psycopg.connect(ADMIN) as c:
   remaining=c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(scope.owner_id,)).fetchone()[0]
   debit=c.execute('SELECT sum(retail_actual) FROM provider_attempts WHERE owner_id=%s',(scope.owner_id,)).fetchone()[0]
  assert debit>0 and remaining==12000-debit

  from chain import AcceptanceChain
  stages={'identity':receipt,'operator':operator,'sandbox_purchase':purchased,'pipeline':proved_pipeline,'gate_edit':result,'final_artifacts':exported,'recovery':recovered}
  chain=AcceptanceChain(cfg,adapters={key:lambda *a:None for key in stages})
  for key,value in stages.items():
   chain.validate(key,value);chain.receipts[key]=value
