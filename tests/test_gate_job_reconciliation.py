"""A waiting gate can retire an older superseded writer, never live work or money."""
import json
import time
import pytest
from delivery_api.tenancy import tenant_context,TenantAccessDenied


def setup_case(w):
 s=w.store;tid=s.create_task('Superseded phase job')['task_id'];s.set_engine_session(tid,'reconcile-'+tid)
 old=s.enqueue_worker(tid,'phase:architecture');cap=s.claim_worker(old)
 from delivery_api.generator_documents import GeneratorDocuments
 docs=GeneratorDocuments(s,cap,old);docs.write('spec/architecture.json',b'{"retained":true}')
 # Fixture copy of the observed stale/new job ordering, no live processes.
 s.update_phase(tid,'architecture','awaiting_approval');s.wait_at_gate(tid,'architecture')
 new=s.enqueue_worker(tid,'phase:architecture');s.claim_worker(new);s.finish_worker(new)
 return s,tid,old,new,docs


def test_dry_run_exact_apply_retains_evidence_and_fences_old_writer(pg_workspace_factory):
 from delivery_api.gate_job_reconciliation import reconcile
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  with s._task_operation_guard('task.gate.decide'):
   before=s.task(tid)
   plan=reconcile(s._execute_native,tid,'architecture',only_job=old)
   assert plan['changes'][0]['job_id']==old and plan['successor_job_id']==new
   assert s.worker_job(old)['state']=='running'
   done=reconcile(s._execute_native,tid,'architecture',apply=True,expected=plan['fingerprint'],only_job=old)
   assert done['applied'] and s.worker_job(old)['state']=='failed'
   assert s.worker_job(new)['state']=='done'
   assert s.task(tid)==before
  with pytest.raises(TenantAccessDenied):docs.write('spec/architecture.json',b'late')
  assert any(e['action']=='worker.superseded_at_gate' for e in s.audit_entries(tid))


def test_changed_fingerprint_rolls_back_and_live_lease_refuses(pg_workspace_factory):
 from delivery_api.gate_job_reconciliation import reconcile
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  with pytest.raises(ValueError,match='plan changed'):
   with s._task_operation_guard('task.gate.decide'):reconcile(s._execute_native,tid,'architecture',apply=True,expected='0'*64)
  assert s.worker_job(old)['state']=='running'
  with s._task_operation_guard('task.gate.decide'):
   s._execute_native('UPDATE engine_sessions SET lease_expires_at=%s WHERE session_id=%s',(time.time()+60,s.engine_session(tid)),write=True)
   with pytest.raises(ValueError,match='lease is still live'):reconcile(s._execute_native,tid,'architecture',apply=True)
   assert s.worker_job(old)['state']=='running'


def test_current_producer_and_wrong_phase_are_not_superseded(pg_workspace_factory):
 from delivery_api.gate_job_reconciliation import reconcile
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  with s._task_operation_guard('task.gate.decide'):
   with pytest.raises(ValueError,match='waiting gate'):reconcile(s._execute_native,tid,'srs',apply=True)
   queued=s.enqueue_worker(tid,'phase:architecture')
   with pytest.raises(ValueError,match='Latest exact phase job must be DONE'):reconcile(s._execute_native,tid,'architecture',apply=True)
   assert s.worker_job(old)['state']=='running'


def test_unsettled_attempt_is_not_silently_retired_or_charged(pg_workspace_factory):
 from delivery_api.gate_job_reconciliation import reconcile
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  with w.admin() as c:
   c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'fixture','{}','fixture','uncertain',1,1,0,0)",('uncertain-'+old,w.scope.owner_id,old))
  with pytest.raises(ValueError,match='unsettled accounting'):
   with s._task_operation_guard('task.gate.decide'):reconcile(s._execute_native,tid,'architecture',apply=True)
  assert s.worker_job(old)['state']=='running'
  with w.admin() as c:assert c.execute('SELECT state FROM provider_attempts WHERE job_id=%s',(old,)).fetchone()[0]=='uncertain'


def test_gate_guard_reconciles_in_same_artifact_transaction_and_rolls_back_on_failure(pg_workspace_factory,monkeypatch):
 from delivery_api.generator_documents import commit_gate_documents,GeneratorDocuments
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  original=s.publish_task_revision_bytes
  monkeypatch.setattr(s,'publish_task_revision_bytes',lambda *a,**k:(_ for _ in ()).throw(ValueError('injected publication failure')))
  with pytest.raises(ValueError,match='injected publication failure'):commit_gate_documents(s,tid,'architecture',{'architecture.json':b'{"retained":true}'})
  assert s.worker_job(old)['state']=='running'
  assert not any(e['action']=='worker.superseded_at_gate' for e in s.audit_entries(tid))
  monkeypatch.setattr(s,'publish_task_revision_bytes',original)
  commit_gate_documents(s,tid,'architecture',{'architecture.json':b'{"retained":true}'})
  assert s.worker_job(old)['state']=='failed'
  assert GeneratorDocuments.owner_reader(s,tid).read('spec/architecture.json')==b'{"retained":true}'
  with pytest.raises(TenantAccessDenied):docs.write('spec/architecture.json',b'late')


def test_standalone_owner_command_dry_run_then_exact_apply(pg_workspace_factory,monkeypatch):
 from pathlib import Path
 import os,subprocess,sys
 command=Path(__file__).resolve().parents[1]/'scripts/ops/reconcile-gate-job.py'
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  env=dict(os.environ,REQUI_ADMIN_DATABASE_URL=w.dsn)
  root=Path(__file__).resolve().parents[1]
  argv=[sys.executable,command,str(root),'--task',tid,'--job',old]
  result=subprocess.run(argv,env=env,text=True,capture_output=True,check=True)
  plan=json.loads(result.stdout.split('\nDRY RUN ONLY.')[0]);assert plan['applied'] is False
  assert s.worker_job(old)['state']=='running'
  result=subprocess.run(argv+['--apply',plan['fingerprint']],env=env,text=True,capture_output=True,check=True)
  assert json.loads(result.stdout)['applied'] is True
  assert s.worker_job(old)['state']=='failed' and s.worker_job(new)['state']=='done'
  assert s.task(tid)['state']=='waiting_gate'


def test_replay_observed_old_running_new_done_metadata_on_disposable_copy(pg_workspace_factory):
 from delivery_api.gate_job_reconciliation import reconcile
 # Captured task diagnostics, not live authority or a claim about current DB state.
 old_id='0df7e3ec057c3b76fb7fdd922dffbad2';new_id='87cd74d5854c83152363fba5bf5da3b1'
 from datetime import datetime
 epoch=lambda value:datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
 with pg_workspace_factory() as w,tenant_context(w.scope):
  s,tid,old,new,docs=setup_case(w)
  with w.admin() as c:
   c.execute('UPDATE worker_jobs SET job_id=%s,created_at=%s,updated_at=%s WHERE job_id=%s',(old_id,epoch('2026-11-13T07:38:57Z'),epoch('2026-11-13T07:39:00Z'),old))
   c.execute('UPDATE worker_jobs SET job_id=%s,created_at=%s,updated_at=%s WHERE job_id=%s',(new_id,epoch('2026-11-14T05:03:29Z'),epoch('2026-11-14T05:20:07Z'),new))
  with s._task_operation_guard('task.gate.decide'):
   plan=reconcile(s._execute_native,tid,'architecture',only_job=old_id)
   assert plan['changes']==[{'job_id':old_id,'from':'running','to':'failed','attempt':1,'updated_at':epoch('2026-11-13T07:39:00Z')}]
   assert plan['successor_job_id']==new_id
   reconcile(s._execute_native,tid,'architecture',only_job=old_id,expected=plan['fingerprint'],apply=True)
  assert s.worker_job(old_id)['state']=='failed' and s.worker_job(new_id)['state']=='done'
  assert s.task(tid)['state']=='waiting_gate'
