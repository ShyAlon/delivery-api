import os
import uuid
import pytest
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope,tenant_context,TenantAccessDenied

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
A=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001')
B=TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002')

@pytest.fixture
def store(): return PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))


def test_create_read_mutate_isolation_and_duplicate_title(store):
    title='test-'+uuid.uuid4().hex
    with tenant_context(A):
        a=store.create_task(title)
        assert a['owner_id']==A.owner_id
        with pytest.raises(ValueError): store.create_task(title.upper())
        event=store.add_event(a['task_id'],'test','private')
        assert event>0
        assert any(e['id']==event for e in store.events(a['task_id']))
    with tenant_context(B):
        b=store.create_task(title)
        assert store.task(a['task_id']) is None
        assert store.events(a['task_id'])==[]
        assert all(t['owner_id']==B.owner_id for t in store.list_tasks())
        with pytest.raises(KeyError): store.rename_task(a['task_id'],'stolen')
        with pytest.raises(Exception): store.add_event(a['task_id'],'attack','bad')
        store.delete_task(b['task_id'])
    with tenant_context(A):
        store.delete_task(a['task_id'])
        assert store.task(a['task_id']) is None
        assert any(e['action']=='task.deleted' for e in store.audit_entries(a['task_id']))


def test_session_binding_owner_and_task_immutable(store):
    session='test-'+uuid.uuid4().hex
    with tenant_context(A):
        a=store.create_task(session)
        other=store.create_task(session+'-other')
        store.set_engine_session(a['task_id'],session)
        assert store.session_binding(session)['task_id']==a['task_id']
        with pytest.raises(TenantAccessDenied): store.set_engine_session(other['task_id'],session)
    with tenant_context(B):
        assert store.session_binding(session) is None
        b=store.create_task(session)
        with pytest.raises(Exception): store.set_engine_session(b['task_id'],session)
        store.delete_task(b['task_id'])
    with tenant_context(A):
        store.delete_task(a['task_id']);store.delete_task(other['task_id'])


def test_no_scope_no_store_reads(store):
    with pytest.raises(TenantAccessDenied):store.list_tasks()


def test_legacy_auth_cannot_provision_on_user_connection(store):
    with tenant_context(A):
        with pytest.raises(TenantAccessDenied):store.create_basic_user('evil','secret',999)
        with pytest.raises(TenantAccessDenied):store.set_user_quota(A.owner_id,999)


def test_session_presence_required_and_revocation_closes_copied_scope(store):
    import psycopg
    with tenant_context(A):
        assert store.auth_session_active()
    unbound=TenantScope(A.owner_id,'00000000-0000-0000-0000-000000000099')
    with tenant_context(unbound):
        assert not store.auth_session_active()
        assert store.list_tasks()==[]
        with pytest.raises(Exception):store.create_task('revoked-'+uuid.uuid4().hex)
    with psycopg.connect(DSN) as admin:
        admin.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
    try:
        with tenant_context(A):
            assert not store.auth_session_active()
            assert store.list_tasks()==[]
    finally:
        with psycopg.connect(DSN) as admin:
            admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(A.session_id,A.owner_id))


def test_concurrent_requests_share_adapter_not_transaction_identity(store):
    from concurrent.futures import ThreadPoolExecutor
    def action(scope):
        with tenant_context(scope):
            title='concurrent-'+uuid.uuid4().hex
            task=store.create_task(title)
            assert all(row['owner_id']==scope.owner_id for row in store.list_tasks())
            assert store.task(task['task_id'])['owner_id']==scope.owner_id
            store.delete_task(task['task_id'])
            return scope.owner_id
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(action,[A,B,A,B]))==[A.owner_id,B.owner_id,A.owner_id,B.owner_id]


def test_duplicate_rollback_leaves_no_partial_children_or_audit(store):
    title='duplicate-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(title)
        before=len(store.audit_entries())
        with pytest.raises(ValueError):store.create_task(title)
        assert len(store.audit_entries())==before
        store.add_event(task['task_id'],'after','connection remains usable')
        assert store.events(task['task_id'])
        store.delete_task(task['task_id'])


def test_worker_capability_preserves_owner_task_session_generation(store):
    from delivery_api.worker_capability import WorkerCapability
    sid='capability-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid)
        store.set_engine_session(task['task_id'],sid)
        capability=WorkerCapability.capture(store,task['task_id'])
        assert capability.validate(store)['task_id']==task['task_id']
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):capability.validate(store)
        with pytest.raises(TenantAccessDenied):WorkerCapability.capture(store,task['task_id'])
    with tenant_context(A):
        store.delete_task(task['task_id'])
        with pytest.raises(TenantAccessDenied):capability.validate(store)


def test_subprocess_namespace_checked_before_any_launch(store,tmp_path,monkeypatch):
    import asyncio
    from delivery_api import config
    from delivery_api.runner import ProcessRunner
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    async def forbidden(*args,**kwargs):raise AssertionError('subprocess must not start')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',forbidden)
    sid='runner-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        runner=ProcessRunner(store)
        with pytest.raises(PermissionError):
            asyncio.run(runner.run(task['task_id'],['fake','--out','/tmp/shared'],cwd=tmp_path,phase='needs'))
        store.delete_task(task['task_id'])


def test_durable_worker_claim_resume_and_cross_owner_denial(store):
    import asyncio
    from delivery_api.tenant_jobs import execute_job
    sid='job-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake-phase')
        capability=store.claim_worker(job)
        assert capability.task_id==task['task_id']
        assert store.claim_worker(job) is None
        store.pause_interrupted_worker(job)
    with tenant_context(B):
        assert store.worker_job(job) is None
        assert store.claim_worker(job) is None
    seen=[]
    async def handler(capability):seen.append((capability.scope.owner_id,capability.session_id))
    with tenant_context(A):
        assert asyncio.run(execute_job(store,job,handler))
        assert store.worker_job(job)['state']=='done'
        assert store.worker_job(job)['attempt']==2
        assert not asyncio.run(execute_job(store,job,handler))
        assert seen==[(A.owner_id,sid)]
        store.delete_task(task['task_id'])


def test_worker_retry_requires_explicit_live_session_reauthorization(store):
    import psycopg
    sid='reauth-'+uuid.uuid4().hex
    new_session=str(uuid.uuid4())
    with psycopg.connect(DSN) as admin:
        admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(new_session,A.owner_id))
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake-phase')
        assert store.claim_worker(job)
        store.finish_worker(job,failed=True)
    new_scope=TenantScope(A.owner_id,new_session)
    with tenant_context(new_scope):
        with pytest.raises(TenantAccessDenied):store.claim_worker(job)
        store.reauthorize_worker(job)
        assert store.claim_worker(job).scope.session_id==new_session
        store.finish_worker(job)
        store.delete_task(task['task_id'])


def test_concurrent_worker_claim_only_one_winner(store):
    from concurrent.futures import ThreadPoolExecutor
    sid='claim-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake')
    def claim(_):
        with tenant_context(A):return store.claim_worker(job) is not None
    with ThreadPoolExecutor(4) as pool:assert sum(pool.map(claim,range(4)))==1
    with tenant_context(A):store.finish_worker(job);store.delete_task(task['task_id'])


def test_scoped_async_job_drives_fake_runner_and_records_only_owner_progress(store,tmp_path,monkeypatch):
    import asyncio
    from delivery_api import config
    from delivery_api.tenant_jobs import execute_job
    from delivery_api.tenancy import current_scope,session_root
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    sid='dispatch-'+uuid.uuid4().hex
    async def fake_runner(capability):
        assert current_scope()==A
        capability.validate(store)
        root=session_root(tmp_path,A,capability.session_id)
        root.mkdir(parents=True)
        (root/'fake-output.json').write_text('{"result":"owner-a"}')
        store.add_event(capability.task_id,'progress','fake worker finished')
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake')
        assert asyncio.run(execute_job(store,job,fake_runner))
        assert store.worker_job(job)['state']=='done'
        assert store.events(task['task_id'])[-1]['message']=='fake worker finished'
    with tenant_context(B):
        assert store.events(task['task_id'])==[]
        assert store.worker_job(job) is None
    with tenant_context(A):store.delete_task(task['task_id'])


def test_failed_async_worker_requires_explicit_retry(store):
    import asyncio
    from delivery_api.tenant_jobs import execute_job
    sid='fail-'+uuid.uuid4().hex
    async def fail(capability):raise RuntimeError('fake handler failed')
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake')
        with pytest.raises(RuntimeError):asyncio.run(execute_job(store,job,fail))
        assert store.worker_job(job)['state']=='failed'
        assert store.claim_worker(job) is None
        store.reauthorize_worker(job)
        assert store.claim_worker(job)
        store.finish_worker(job)
        store.delete_task(task['task_id'])


def test_phase_dispatch_seam_uses_durable_job_and_explicit_matching_resume(store):
    import asyncio
    from delivery_api.tenant_jobs import dispatch_phase
    sid='phase-'+uuid.uuid4().hex
    calls=[]
    class FakeOrchestrator:
        async def execute_phase(self,task_id,phase):calls.append((task_id,phase))
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=asyncio.run(dispatch_phase(store,FakeOrchestrator(),task['task_id'],'needs'))
        assert store.worker_job(job)['state']=='done'
        assert calls==[(task['task_id'],'needs')]
        with pytest.raises(PermissionError):asyncio.run(dispatch_phase(store,FakeOrchestrator(),task['task_id'],'srs',resume_job_id=job))
        store.delete_task(task['task_id'])

@pytest.mark.parametrize('phase',['needs','srs'])
def test_atomic_retry_reservation_rolls_back_and_concurrent_confirm_has_one_job(store,monkeypatch,phase):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.phases import PHASE_KEYS
    sid='atomic-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        for key in PHASE_KEYS[:PHASE_KEYS.index(phase)]:store.update_phase(tid,key,'approved')
        store.update_phase(tid,phase,'failed')
        before=store.task(tid);events=store.events(tid);audits=store.audit_entries(tid)
        original=store.enqueue_worker
        def fail(*args,**kw):raise RuntimeError('fake enqueue failure')
        monkeypatch.setattr(store,'enqueue_worker',fail)
        with pytest.raises(RuntimeError):store.prepare_phase_retry(tid,phase,'owner','ollama:phi4',False)
        assert store.task(tid)==before and store.events(tid)==events and store.audit_entries(tid)==audits
        monkeypatch.setattr(store,'enqueue_worker',original)
    with tenant_context(B):assert store.prepare_phase_retry(tid,phase,'foreign','ollama:phi4',False) is None
    def reserve(_):
        connection=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return connection.prepare_phase_retry(tid,phase,'owner','ollama:phi4',False)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
    assert sum(result is not None for result in results)==1
    with tenant_context(A):
        with store._postgres.transaction() as connection:
            rows=connection.execute('SELECT * FROM worker_jobs WHERE task_id=%s',(tid,)).fetchall()
        assert len(rows)==1 and rows[0]['state']=='pending'
        assert store.task(tid)['model_default']=='ollama:phi4'
        store.update_phase(tid,phase,'failed');store.delete_task(tid)


def test_durable_worker_options_cannot_be_overridden_and_invalid_shapes_rejected(store):
    import asyncio
    from delivery_api.tenant_jobs import dispatch_phase
    from delivery_api.orchestrator import ExecutionMode
    sid='options-'+uuid.uuid4().hex
    class Never:
        async def execute_phase(self,*args,**kw):raise AssertionError('should not execute')
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:needs',execution_options={'mode':'resume','model_route':'ollama:phi4'})
        with pytest.raises(ValueError):asyncio.run(dispatch_phase(store,Never(),tid,'needs',queued_job_id=job,mode=ExecutionMode.START))
        assert store.worker_job(job)['state']=='pending'
        for bad in ({'mode':None},{'mode':'unexpected'},{'extra':True},{'model_route':'bad'},{'model_route':None}):
            with pytest.raises(Exception):store.enqueue_worker(tid,'phase:needs',execution_options=bad)
        store.delete_task(tid)

@pytest.mark.parametrize('action',['approve','redo'])
def test_atomic_gate_handoff_and_redo_job_options(store,monkeypatch,action):
    sid='gate-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        store.update_phase(tid,'needs','running');gate=store.wait_at_gate(tid,'needs')
        decision={'selected_option':'approved'} if action=='approve' else {'steer':'fix evidence'}
        before=store.task(tid);events=store.events(tid)
        original=store.enqueue_worker
        def fail(*args,**kw):raise RuntimeError('enqueue failed')
        monkeypatch.setattr(store,'enqueue_worker',fail)
        with pytest.raises(RuntimeError):store.prepare_gate_dispatch(tid,gate['gate_key'],action,actor='owner',model='ollama:phi4',**decision)
        assert store.task(tid)==before and store.events(tid)==events
        monkeypatch.setattr(store,'enqueue_worker',original)
        result,job,phase=store.prepare_gate_dispatch(tid,gate['gate_key'],action,actor='owner',model='ollama:phi4',**decision)
        assert phase==('needs' if action=='redo' else 'srs')
        assert store.worker_job(job)['execution_options']=={'mode':'redo' if action=='redo' else 'start','model_route':'ollama:phi4'}
        with pytest.raises(ValueError):store.prepare_gate_dispatch(tid,gate['gate_key'],action,actor='owner',model='ollama:phi4',**decision)
        store.update_phase(tid,'needs','failed');store.delete_task(tid)


def test_resume_cannot_reauthorize_running_job_and_dispatch_cannot_report_lost_claim(store,monkeypatch):
    import asyncio
    from delivery_api.tenant_jobs import dispatch_phase
    sid='lost-claim-'+uuid.uuid4().hex
    class Never:
        async def execute_phase(self,*args,**kw):raise AssertionError('lost claim must not launch')
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:needs')
        original=store.claim_worker
        def race(job_id):
            assert original(job_id) is not None
            return None
        monkeypatch.setattr(store,'claim_worker',race)
        with pytest.raises(ValueError,match='already claimed'):
            asyncio.run(dispatch_phase(store,Never(),tid,'needs',queued_job_id=job))
        assert store.worker_job(job)['state']=='running'
        with pytest.raises(ValueError):store.reauthorize_worker(job)
        assert store.worker_job(job)['attempt']==1
        store.finish_worker(job);store.delete_task(tid)


def test_cloud_subprocess_without_reviewed_gateway_stops_before_phase_or_child(store,tmp_path,monkeypatch):
    import asyncio
    from delivery_api.runner import ProcessRunner
    from delivery_api import config
    from delivery_api.tenancy import session_root
    from delivery_api.attempt_ledger import AdmissionDenied
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    sid='runner-admission-'+uuid.uuid4().hex
    async def forbidden(*args,**kw):raise AssertionError('must not create subprocess')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',forbidden)
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        before=store.task(tid);events=store.events(tid)
        root=session_root(tmp_path,A,sid)
        runner=ProcessRunner(store)
        with pytest.raises(AdmissionDenied,match='gateway'):
            asyncio.run(runner.run(tid,['fake','--out',str(root/'spec')],cwd=tmp_path,phase='needs',env={'VMODEL_ARTIFACTS_DIR':str(root.parent)}))
        assert store.task(tid)==before and store.events(tid)==events
        store.delete_task(tid)


@pytest.mark.parametrize('verified',[False,True])
def test_stopped_dispatch_return_requires_explicit_writer_quiescence(store,verified):
    import asyncio
    from delivery_api.tenant_jobs import execute_job
    from delivery_api.attempt_ledger import AdmissionDenied
    sid='stop-dispatch-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'fake')
        async def handler(capability):store.request_task_stop(tid)
        assert asyncio.run(execute_job(store,job,handler,verify_stopped_quiescence=lambda cap:verified))
        assert store.worker_job(job)['state']==('done' if verified else 'running')
        assert store.task(tid)['state']=='stop_requested'
        if not verified:
            with pytest.raises(AdmissionDenied):store.confirm_task_stopped(tid,engine_quiescent=True)
            store.finish_stopped_worker(job,worker_quiescent=True)
        store.confirm_task_stopped(tid,engine_quiescent=True);store.delete_task(tid)


def test_stopped_dispatch_failure_stays_pending_never_failed(store):
    import asyncio
    from delivery_api.tenant_jobs import execute_job
    sid='stop-error-'+uuid.uuid4().hex
    with tenant_context(A):
        tid=store.create_task(sid)['task_id'];store.set_engine_session(tid,sid);job=store.enqueue_worker(tid,'fake')
        async def handler(capability):
            store.request_task_stop(tid)
            raise RuntimeError('fake accepted provider still alive')
        with pytest.raises(RuntimeError):asyncio.run(execute_job(store,job,handler))
        assert store.worker_job(job)['state']=='running' and store.task(tid)['state']=='stop_requested'
        store.finish_stopped_worker(job,worker_quiescent=True);store.confirm_task_stopped(tid,engine_quiescent=True);store.delete_task(tid)


def test_operations_snapshot_never_turns_admin_need_into_global_rls_access(store):
    with tenant_context(A):
        tid=store.create_task('ops-'+uuid.uuid4().hex)['task_id'];sid='ops-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        got=store.operator_workspace_snapshot()
        assert got['owner_id']==A.owner_id and any(row['session_id']==sid for row in got['sessions'])
        assert not got['global_operator_access'] and not got['credit_adjustment_available']
    with tenant_context(B):assert not any(row['session_id']==sid for row in store.operator_workspace_snapshot()['sessions'])
    with tenant_context(A):store.delete_task(tid)


def test_task_policy_state_owner_read_only_and_evidence_private(store):
    import psycopg
    with psycopg.connect(DSN) as c:c.execute('INSERT INTO user_task_policy VALUES (%s,true,1,0)',(A.owner_id,))
    try:
        for scope,expected in ((A,1),(B,0)):
            with tenant_context(scope):
                with store._postgres.transaction() as c:
                    assert len(c.execute('SELECT * FROM user_task_policy').fetchall())==expected
                for query in ('UPDATE user_task_policy SET suspended=false','INSERT INTO user_task_policy VALUES (auth.uid(),false,2,0)',
                    'DELETE FROM user_task_policy','SELECT * FROM user_task_policy_changes'):
                    with store._postgres.transaction() as c:
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(query)
                for role in ('authenticated','requi_operator_read','requi_operator_usage'):
                    with store._postgres.transaction() as c:
                        c.execute('SET LOCAL ROLE '+role)
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM user_task_policy')
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM user_task_policy WHERE owner_id=%s',(A.owner_id,))


def test_cloud_store_rerun_cannot_bypass_missing_generation_lifecycle(store):
    with tenant_context(A):
        tid=store.create_task('rerun-lifecycle-'+uuid.uuid4().hex)['task_id']
        session='rerun-session-'+uuid.uuid4().hex;store.set_engine_session(tid,session)
        before=store.task(tid);binding=store.session_binding(session);audit=store.audit_entries(tid)
        for phase in ('intent','needs','srs'):
            with pytest.raises(TenantAccessDenied,match='generation/session'):
                store.prepare_rerun(tid,phase)
            assert store.task(tid)==before and store.session_binding(session)==binding
            assert store.audit_entries(tid)==audit
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):store.prepare_rerun(tid,'needs')
    with tenant_context(A):store.delete_task(tid)


def test_task_file_guard_keeps_owner_lock_across_nested_store_work(store):
    import hashlib,psycopg
    from concurrent.futures import ThreadPoolExecutor
    key=int.from_bytes(hashlib.sha256(A.owner_id.encode()).digest()[:8],'big',signed=True)
    def lock_available():
        with psycopg.connect(DSN) as c:
            return c.execute('SELECT pg_try_advisory_xact_lock(%s)',(key,)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_context(A):
            with store._task_operation_guard('task.gate.decide'):
                assert store.auth_session_active()
                assert pool.submit(lock_available).result(timeout=5) is False
            assert pool.submit(lock_available).result(timeout=5) is True
            with pytest.raises(RuntimeError):
                with store._task_operation_guard('task.gate.decide'):
                    raise RuntimeError('fake file failure')
            assert pool.submit(lock_available).result(timeout=5) is True


def test_retained_review_conflict_is_not_silently_accepted(store):
    with tenant_context(A):
        tid=store.create_task('review-replay-'+uuid.uuid4().hex)['task_id']
        payload={'evidence':'original'}
        store.retain_decision_review(tid,'gate:gate-needs','same',payload)
        store.retain_decision_review(tid,'gate:gate-needs','same',payload)
        with pytest.raises(ValueError,match='conflicting'):
            store.retain_decision_review(tid,'gate:gate-needs','same',{'evidence':'changed'})
        assert store.decision_review(tid,'gate:gate-needs','same')==payload
    with tenant_context(B):
        assert store.decision_review(tid,'gate:gate-needs','same') is None
        with pytest.raises(Exception):store.retain_decision_review(tid,'gate:gate-needs','same',{'evidence':'foreign'})
    with tenant_context(A):store.delete_task(tid)
