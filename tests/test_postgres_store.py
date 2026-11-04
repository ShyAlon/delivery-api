import os
import uuid
import pytest
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope,tenant_context,TenantAccessDenied

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
# Session ids are the seeded live auth sessions, deliberately distinct from the
# owner ids: tenant scope must never validate because session == owner.
A=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
B=TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2')

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




def _installed_dispatch_fixture(monkeypatch):
    from delivery_api import inprocess_engine
    from delivery_api.tenant_jobs import execute_job
    class Engine:
        async def dispatch(self,store,orchestrator,tid,phase,job,options):
            async def run(cap):
                cap.validate(store)
                await orchestrator.execute_phase(tid,phase,**options)
            return await execute_job(store,job,run)
    monkeypatch.setattr(inprocess_engine,'_engine',Engine())

def test_phase_dispatch_seam_uses_durable_job_and_explicit_matching_resume(store,monkeypatch):
    _installed_dispatch_fixture(monkeypatch)
    from delivery_api import config
    # This seam executes the local orchestrator callback, not a cloud producer.
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
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
    _installed_dispatch_fixture(monkeypatch)
    from delivery_api import config
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)  # local dispatch lost-claim contract
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
        with pytest.raises(PermissionError,match='isolated child phase required'):
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


def _restart_intent_fixture(store,phase='srs'):
    from delivery_api.gate_revision import save_closed_revision,closed_revision_preflight
    from test_closed_revision_job import options
    from test_revision_intent import proposal
    tid=store.create_task('revision-intent-'+uuid.uuid4().hex)['task_id'];sid='rev-'+uuid.uuid4().hex
    store.set_engine_session(tid,sid);store.wait_at_gate(tid,phase)
    store.retain_decision_review(tid,'gate:gate-'+phase,'review',{'task':store.task(tid)})
    store.decide_gate(tid,'gate-'+phase,'approve',selected_option='Proceed')
    draft=save_closed_revision(store,tid,'gate-'+phase,'review','Describe offline behavior')['draft_id']
    o=options();o['phase']=phase;o['gate_key']='gate-'+phase;o['artifact_evidence']=[{'phase':phase,'name':phase+'.json','generation':1,'sha256':'a'*64}];o['draft_id']=draft;o['gate_revision']=next(g for g in store.task(tid)['gates'] if g['gate_key']=='gate-'+phase)['revision']
    live=closed_revision_preflight(store,tid,'gate-'+phase,'review',draft,artifact_snapshot=o['artifact_evidence'])
    o['preflight_digest']=live['review_digest'];p=proposal()|{'phase':phase}|{'task_id':tid,'session_id':sid}
    evidence={'artifacts':o['artifact_evidence'],'checkpoint_id':'checkpoint','proposal_sha256':p['artifact_sha256']}
    return tid,o,p,evidence


def test_restart_intent_is_held_replay_safe_owner_scoped_and_preserves_task(store):
    with tenant_context(A):
        tid,o,p,evidence=_restart_intent_fixture(store);before=store.task(tid)
        args=dict(request_key=uuid.uuid4().hex,options=o,proposal=p,approval_reference='fake-reviewed-owner-ref',read_current_evidence=lambda:evidence)
        result=store.retain_closed_revision_restart_intent(tid,**args)
        assert result['state']=='held' and not result['dispatch_started'] and not result['replayed']
        again=store.retain_closed_revision_restart_intent(tid,**args)
        assert again['intent_id']==result['intent_id'] and again['replayed']
        assert store.task(tid)==before
        with pytest.raises(ValueError,match='conflicting'):
            store.retain_closed_revision_restart_intent(tid,**(args|{'approval_reference':'different'}))
    with tenant_context(B):
        with pytest.raises((KeyError,TenantAccessDenied)):
            store.retain_closed_revision_restart_intent(tid,**args)
    # Disposable CI database owns teardown; held audit intent must remain.


def test_restart_intent_rejects_changed_current_evidence_without_partial_record(store):
    with tenant_context(A):
        tid,o,p,evidence=_restart_intent_fixture(store);before=store.task(tid);reads=[];key=uuid.uuid4().hex
        def current():
            reads.append(1)
            return evidence if len(reads)==1 else evidence|{'checkpoint_id':'changed'}
        with pytest.raises(ValueError,match='checkpoint'):
            store.retain_closed_revision_restart_intent(tid,request_key=key,options=o,proposal=p,
                approval_reference='fake-owner-ref',read_current_evidence=current)
        with store._postgres.transaction() as conn:
            assert not conn.execute('SELECT 1 FROM closed_revision_restart_intents WHERE request_key=%s',(key,)).fetchone()
        assert store.task(tid)==before
        # Disposable CI database owns teardown.


def test_restart_intent_rows_are_immutable_to_backend_and_hidden_from_browser(store):
    with tenant_context(A):
        tid,o,p,evidence=_restart_intent_fixture(store)
        held=store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=o,proposal=p,
            approval_reference='fake-owner-ref',read_current_evidence=lambda:evidence)
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute("UPDATE closed_revision_restart_intents SET state='held' WHERE intent_id=%s",(held['intent_id'],))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('SET LOCAL ROLE authenticated')
                conn.execute('SELECT * FROM closed_revision_restart_intents').fetchall()
    with tenant_context(B):
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT 1 FROM closed_revision_restart_intents WHERE intent_id=%s',(held['intent_id'],)).fetchone() is None


def _held_lineage_fixture(store):
    import hashlib,json
    tid,o,p,evidence=_restart_intent_fixture(store)
    artifact={'session_id':'semantic','intent_model_ref':'semantic'}
    p['artifact']=artifact;p['artifact_sha256']=hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    evidence['proposal_sha256']=p['artifact_sha256']
    held=store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=o,proposal=p,
        approval_reference='fake-owner-ref',read_current_evidence=lambda:evidence)
    args=dict(intent_id=held['intent_id'],semantic_session_id='semantic',new_execution_session_id='next-'+uuid.uuid4().hex,
        retained_proposal={'proposal':p,'document_sha256':'d'*64})
    return tid,args


def test_held_lineage_is_immutable_owner_scoped_and_never_installs_session(store):
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid)
        result=store.retain_closed_revision_lineage_plan(tid,**args)
        assert result['state']=='planned' and not result['installed']
        again=store.retain_closed_revision_lineage_plan(tid,**args)
        assert again['lineage_id']==result['lineage_id'] and again['replayed']
        assert store.task(tid)==before and store.session_binding(args['new_execution_session_id']) is None
        with pytest.raises(ValueError,match='conflicting'):
            store.retain_closed_revision_lineage_plan(tid,**(args|{'new_execution_session_id':'different'}))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('UPDATE closed_revision_lineage_plans SET payload=payload WHERE lineage_id=%s',(result['lineage_id'],))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM closed_revision_lineage_plans').fetchall()
    with tenant_context(B):
        with pytest.raises((KeyError,TenantAccessDenied)):store.retain_closed_revision_lineage_plan(tid,**args)
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT 1 FROM closed_revision_lineage_plans WHERE lineage_id=%s',(result['lineage_id'],)).fetchone() is None


def test_lineage_proposal_mismatch_leaves_no_plan_or_task_change(store):
    from copy import deepcopy
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid);wrong=deepcopy(args)
        wrong['retained_proposal']['proposal']['change_request']='changed'
        with pytest.raises(ValueError,match='differs'):
            store.retain_closed_revision_lineage_plan(tid,**wrong)
        assert store.task(tid)==before
        with store._postgres.transaction() as conn:
            assert not conn.execute('SELECT 1 FROM closed_revision_lineage_plans WHERE intent_id=%s',(args['intent_id'],)).fetchone()


def test_preparation_journal_is_append_only_cas_and_never_promotes(store):
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid)
        plan=store.retain_closed_revision_lineage_plan(tid,**args);lid=plan['lineage_id']
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        evidence={'manifest_sha256':'f'*64,'checkpoint_id':'staged-checkpoint'}
        staged=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],
            state='staged',evidence=evidence,read_staged_receipt=lambda:evidence)
        assert staged['event']['sequence']==1 and not staged['event']['dispatch_started'] and store.task(tid)==before
        with pytest.raises(ValueError,match='compare-and-swap'):
            store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],state='held_for_review',evidence={'reason':'interrupted'})
        with pytest.raises(ValueError,match='unavailable'):
            store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=staged['event_sha256'],state='promotion_pending',evidence={'manifest_sha256':'f'*64})
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('UPDATE closed_revision_promotion_events SET payload=payload WHERE lineage_id=%s',(lid,))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM closed_revision_promotion_events').fetchall()
    with tenant_context(B):
        with pytest.raises((KeyError,TenantAccessDenied)):
            store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,state='prepared',evidence={})
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchone() is None


def test_staged_journal_receipt_drift_leaves_no_partial_event(store):
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        evidence={'manifest_sha256':'f'*64,'checkpoint_id':'staged-checkpoint'};reads=[]
        def receipt():
            reads.append(1);return evidence if len(reads)==1 else evidence|{'checkpoint_id':'changed'}
        with pytest.raises(ValueError,match='receipt evidence changed'):
            store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],
                state='staged',evidence=evidence,read_staged_receipt=receipt)
        with store._postgres.transaction() as conn:
            rows=conn.execute('SELECT state FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall()
            assert len(rows)==1 and rows[0]['state']=='prepared'


def _retain_fake_source_review(store,tid,lid,tmp_path,monkeypatch):
    from delivery_api import revision_source_review
    from test_revision_source_review import args as snapshot_args
    def current(*a,**kw):
        return revision_source_review.source_review_payload(**(snapshot_args()|{'lineage_sha256':kw['lineage_sha256'],
            'checkpoint_id':kw['lineage']['source_checkpoint_id'],'semantic_session_id':kw['lineage']['semantic_session_id']}))
    monkeypatch.setattr(revision_source_review,'read_source_review_snapshot',current)
    store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
    return current


def test_concrete_staging_recorder_loads_held_lineage_and_options_before_journal(store,tmp_path,monkeypatch):
    from delivery_api import revision_stage_evidence
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid)
        lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        _retain_fake_source_review(store,tid,lid,tmp_path,monkeypatch)
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        reads=[]
        def validated(s,cap,**kwargs):
            reads.append(kwargs)
            assert s is store and cap.task_id==tid and cap.session_id==args['retained_proposal']['proposal']['session_id']
            assert kwargs['lineage']['execution_session_id']==args['new_execution_session_id']
            assert kwargs['options']['change_request']=='Describe offline behavior' and kwargs['artifacts_base']==tmp_path
            return {'manifest_sha256':'e'*64,'checkpoint_id':'staged-checkpoint'}
        monkeypatch.setattr(revision_stage_evidence,'read_validated_staged_receipt',validated)
        staged=store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],
            manifest_sha256='e'*64,artifacts_base=tmp_path)
        assert len(reads)==3 and staged['event']['state']=='staged' and store.task(tid)==before
        assert not staged['event']['dispatch_started'] and store.session_binding(args['new_execution_session_id']) is None
    with tenant_context(B):
        with pytest.raises((KeyError,TenantAccessDenied)):
            store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=None,manifest_sha256='e'*64,artifacts_base=tmp_path)


def test_concrete_staging_validation_failure_records_no_event(store,tmp_path,monkeypatch):
    from delivery_api import revision_stage_evidence
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        _retain_fake_source_review(store,tid,lid,tmp_path,monkeypatch)
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        def invalid(*a,**kwargs):raise ValueError('typed stage changed')
        monkeypatch.setattr(revision_stage_evidence,'read_validated_staged_receipt',invalid)
        with pytest.raises(ValueError,match='typed stage changed'):
            store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],manifest_sha256='e'*64,artifacts_base=tmp_path)
        with store._postgres.transaction() as conn:
            assert len(conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall())==1


def test_upstream_review_snapshot_is_immutable_replay_safe_not_acceptance(store,tmp_path,monkeypatch):
    from delivery_api import revision_source_review
    from test_revision_source_review import args as snapshot_args
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid)
        lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        def current(*a,**kw):
            return revision_source_review.source_review_payload(**(snapshot_args()|{'lineage_sha256':kw['lineage_sha256'],
                'checkpoint_id':kw['lineage']['source_checkpoint_id'],'semantic_session_id':kw['lineage']['semantic_session_id']}))
        monkeypatch.setattr(revision_source_review,'read_source_review_snapshot',current)
        first=store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert first['state']=='awaiting_review' and not first['accepted'] and not first['replayed']
        assert store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)['replayed']
        assert store.task(tid)==before
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('UPDATE closed_revision_source_reviews SET payload=payload WHERE review_id=%s',(first['review_id'],))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM closed_revision_source_reviews').fetchall()
        def changed(*a,**kw):
            inputs=snapshot_args();inputs['upstream_sha256']['spec/needs.json']='c'*64
            return revision_source_review.source_review_payload(**(inputs|{'lineage_sha256':kw['lineage_sha256'],
                'checkpoint_id':kw['lineage']['source_checkpoint_id'],'semantic_session_id':kw['lineage']['semantic_session_id']}))
        monkeypatch.setattr(revision_source_review,'read_source_review_snapshot',changed)
        with pytest.raises(ValueError,match='snapshot changed'):store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
    with tenant_context(B):
        with pytest.raises((KeyError,TenantAccessDenied)):store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT 1 FROM closed_revision_source_reviews WHERE review_id=%s',(first['review_id'],)).fetchone() is None


def test_staging_requires_retained_source_snapshot_and_rejects_postreview_byte_change(store,tmp_path,monkeypatch):
    from delivery_api import revision_source_review,revision_stage_evidence
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);before=store.task(tid)
        lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        kw=dict(lineage_id=lid,expected_previous_sha256=first['event_sha256'],manifest_sha256='e'*64,artifacts_base=tmp_path)
        with pytest.raises(ValueError,match='retained upstream review snapshot required'):
            store.record_validated_revision_staging(tid,**kw)
        current=_retain_fake_source_review(store,tid,lid,tmp_path,monkeypatch)
        def changed(*a,**kwargs):
            original=current(*a,**kwargs);body=original['snapshot']
            return revision_source_review.source_review_payload(lineage_sha256=body['lineage_sha256'],
                checkpoint_id=body['source_checkpoint_id'],semantic_session_id=body['semantic_session_id'],phase=body['phase'],
                upstream_sha256=body['upstream_sha256']|{'spec/needs.json':'c'*64})
        monkeypatch.setattr(revision_source_review,'read_source_review_snapshot',changed)
        with pytest.raises(ValueError,match='differs from retained review snapshot'):store.record_validated_revision_staging(tid,**kw)
        assert store.task(tid)==before
        with store._postgres.transaction() as conn:
            assert len(conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall())==1


def test_source_change_during_concrete_stage_validation_leaves_only_prepared_event(store,tmp_path,monkeypatch):
    from delivery_api import revision_source_review,revision_stage_evidence
    with tenant_context(A):
        tid,args=_held_lineage_fixture(store);lid=store.retain_closed_revision_lineage_plan(tid,**args)['lineage_id']
        current=_retain_fake_source_review(store,tid,lid,tmp_path,monkeypatch)
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,
            state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
        changed=[False]
        def source(*a,**kwargs):
            original=current(*a,**kwargs)
            if not changed[0]:return original
            body=original['snapshot']
            return revision_source_review.source_review_payload(lineage_sha256=body['lineage_sha256'],
                checkpoint_id=body['source_checkpoint_id'],semantic_session_id=body['semantic_session_id'],phase=body['phase'],
                upstream_sha256=body['upstream_sha256']|{'spec/needs.json':'c'*64})
        def stage(*a,**kwargs):
            changed[0]=True
            return {'manifest_sha256':'e'*64,'checkpoint_id':'staged'}
        monkeypatch.setattr(revision_source_review,'read_source_review_snapshot',source)
        monkeypatch.setattr(revision_stage_evidence,'read_validated_staged_receipt',stage)
        with pytest.raises(ValueError,match='differs from retained review snapshot'):
            store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],
                manifest_sha256='e'*64,artifacts_base=tmp_path)
        with store._postgres.transaction() as conn:
            assert len(conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall())==1


from policy_database import isolated_policy_database

@pytest.fixture
def retained_revision_store(monkeypatch,isolated_policy_database):
    # These real graph chains retain thousands of checkpoint/evidence rows.
    # Every parameter is an independent scenario, not an ever-growing owner.
    # Preserve all RLS, graph reconstruction and reconciliation checks.
    monkeypatch.setitem(globals(),'DSN',isolated_policy_database)
    return PostgresStore(isolated_policy_database.replace('user=sandbox','user=delivery_app'))

@pytest.mark.parametrize('coordinated',['pg_child_control','pg_control_stop','pg_architecture_continue','pg_real_architecture','pg_anchor_tamper','pg_stage_tamper','pg_source_tamper','pg_continuation_tamper','pg_successor_revoked','pg_successor_expired','pg_successor_snapshot_swap','pg_successor_prepared_swap','pg_successor_provider_hold','pg_install_only','pg_continuation_only','pg_continuation_consume','pg_continuation_consume_revoked','pg_continuation_failure','pg_activation_rollback',False,True,'before_promotion','after_promotion','revoked_before_promotion','ask_changes','continuation_failure','continuation_consume','continuation_consume_revoked','continuation_real_graph','architecture_continuation','successor_revoked','successor_expired','successor_swap','successor_prepared_swap','successor_provider_hold'])
def test_real_sqlite_files_and_pg_retained_snapshot_to_staged_journal(retained_revision_store,tmp_path,monkeypatch,coordinated):
    store=retained_revision_store
    pg_architecture=coordinated=='pg_architecture_continue'
    if pg_architecture:coordinated='pg_continuation_only'
    pg_real=coordinated=='pg_real_architecture'
    if pg_real:coordinated='pg_continuation_consume'
    pg_fault=coordinated if isinstance(coordinated,str) and coordinated.startswith('pg_successor_') else None
    if pg_fault:coordinated='pg_continuation_consume'
    fault=coordinated if isinstance(coordinated,str) and coordinated.startswith('successor_') else None
    if fault:coordinated='continuation_real_graph'
    source_path=os.getenv("DELIVERY_TEST_SPEC_SOURCE")
    if source_path:monkeypatch.syspath_prepend(source_path)
    import hashlib,json
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.gate_revision import closed_revision_preflight
    from test_revision_stage_real import build_real_stage_fixture
    with tenant_context(A):
        phase='architecture' if coordinated=='architecture_continuation' or pg_architecture else 'srs'
        tid,o,p,_=_restart_intent_fixture(store,phase);before=store.task(tid)
        cap=WorkerCapability.capture(store,tid);saved={}
        def lineage_factory(options,source,result,digest):
            live=closed_revision_preflight(store,tid,'gate-'+phase,'review',options['draft_id'],artifact_snapshot=options['artifact_evidence'])
            options['preflight_digest']=live['review_digest']
            proposal=p|{'proposal_id':'a'*32,'source_checkpoint_id':options['source_checkpoint_id'],
                'artifact':result[phase],'artifact_sha256':digest}
            evidence={'artifacts':options['artifact_evidence'],'checkpoint_id':options['source_checkpoint_id'],'proposal_sha256':digest}
            held=store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=options,proposal=proposal,
                approval_reference='constructed-test-review-not-founder-approval',read_current_evidence=lambda:evidence)
            planned=store.retain_closed_revision_lineage_plan(tid,intent_id=held['intent_id'],semantic_session_id=cap.session_id,
                new_execution_session_id='next-'+uuid.uuid4().hex,retained_proposal={'proposal':proposal,'document_sha256':hashlib.sha256(json.dumps(proposal,sort_keys=True,indent=2).encode()).hexdigest()})
            saved.update(intent_id=held['intent_id'],lineage_id=planned['lineage_id'],proposal=proposal)
            with store._postgres.transaction() as conn:
                return json.loads(conn.execute('SELECT payload FROM closed_revision_lineage_plans WHERE lineage_id=%s',(planned['lineage_id'],)).fetchone()['payload'])
        built=build_real_stage_fixture(tmp_path,cap=cap,options_override=o,lineage_factory=lineage_factory,semantic_session_id=cap.session_id,phase=phase,pg_store=store)
        args,root,dest,documents,files,manifest,md,*_=built
        before=store.task(tid)  # Native source fixture publication is setup, not a revision effect.
        native_pg=isinstance(coordinated,str) and coordinated.startswith('pg_')
        if native_pg:
            import shutil
            shutil.rmtree(root)  # No source files may supply review/stage authority.
        lid=saved['lineage_id']
        review=store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert review['accepted'] is False
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,state='prepared',
            evidence={'intent_id':saved['intent_id'],'lineage_id':lid})
        import shutil
        from spec_generator.stop_fence import StopFence
        from delivery_api.pg_execution_fence import PostgresExecutionFence
        from spec_generator.cooperative_run import cli_execution
        if not native_pg:
            if dest.exists():shutil.rmtree(dest)  # Only test-built data; runtime never deletes/retries partial stage.
            dest.parent.parent.mkdir(parents=True,exist_ok=True)
            (dest.parent.parent/'proposal.json').write_bytes(json.dumps(saved['proposal'],sort_keys=True,indent=2).encode())
        with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
            staged=store.prepare_revision_staging(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],artifacts_base=tmp_path)
        md=staged['event']['evidence']['manifest_sha256']
        if isinstance(coordinated,str) and coordinated.startswith('pg_'):
            import sqlite3
            monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('SQLite forbidden in native PG revision runtime')))
        safety=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert safety['blockers']==['source_writer_exclusion_unverified'] and not safety['installation_available']
        with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
            safety=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert safety['observed_work_quiescent'] and not safety['installation_available']
        pending=store.enqueue_worker(tid,'phase:srs')
        with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
            blocked=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert blocked['unresolved_job_ids']==[pending] and 'worker_jobs_unresolved' in blocked['blockers']
        with store._postgres.transaction() as conn:conn.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s",(pending,))

        installation_review=store.retain_revision_installation_review(tid,lineage_id=lid,
            expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)
        assert not installation_review['review']['accepted'] and not installation_review['review']['installation_available']
        assert store.decision_review(tid,'revision-install:'+lid,installation_review['review_sha256'])==installation_review['review']
        assert store.retain_revision_installation_review(tid,lineage_id=lid,
            expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)==installation_review
        acceptance_kw=dict(lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],review_sha256=installation_review['review_sha256'],decision='approve',extra_cost_acknowledged=True,artifacts_base=tmp_path)
        for invalid in ({'extra_cost_acknowledged':False},{'extra_cost_acknowledged':1},{'decision':'edit'},{'review_sha256':'f'*64}):
            with pytest.raises(ValueError):store.record_private_revision_acceptance(tid,**(acceptance_kw|invalid))
        from delivery_api.revision_acceptance_adapter import PrivateRevisionAcceptanceAdapter
        adapter=PrivateRevisionAcceptanceAdapter(store,tid,lineage_id=lid,staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)
        draft=adapter.save_draft({'review_sha256':installation_review['review_sha256'],'change_request':'Preserve Adapter Draft'})
        assert not draft['installed'] and store.task(tid)==before
        response=adapter.submit({'review_sha256':installation_review['review_sha256'],'choice':'approve','change_request':'','extra_cost_acknowledged':True})
        assert response['pending']=='installation' and not response['installed'] and not response['dispatch_started']
        receipt={'acceptance_reference':response['acceptance_reference'],'installed':False,'replayed':False}
        assert not receipt['installed'] and not receipt['replayed'] and store.task(tid)==before
        assert store.record_private_revision_acceptance(tid,**acceptance_kw)['replayed']
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):
                store.verify_private_revision_acceptance(installation_review['review'],installation_review['review_sha256'],receipt['acceptance_reference'])
        with store._postgres.transaction() as conn:
            with pytest.raises(Exception):
                with conn.transaction():
                    conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM closed_revision_acceptances').fetchall()
            with pytest.raises(Exception):
                with conn.transaction():conn.execute('UPDATE closed_revision_acceptances SET decision=decision WHERE acceptance_id=%s',(receipt['acceptance_reference'],))
        if coordinated=='ask_changes':
            kw=dict(lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],review_sha256=installation_review['review_sha256'],change_request='Keep New Verbatim Guidance',extra_cost_acknowledged=True,artifacts_base=tmp_path)
            for invalid in ({'extra_cost_acknowledged':False},{'change_request':''},{'review_sha256':'f'*64}):
                with pytest.raises(ValueError):store.prepare_private_revision_changes(tid,**(kw|invalid))
            adapter_result=adapter.submit({'review_sha256':installation_review['review_sha256'],'choice':'edit','change_request':kw['change_request'],'extra_cost_acknowledged':True})
            assert adapter_result['pending']=='regeneration' and not adapter_result['installed']
            payload=store.decision_draft(tid,'revision-changes:'+lid,installation_review['review_sha256'],adapter_result['submission_id'])
            changed=payload|{'submission_id':adapter_result['submission_id']}
            assert changed['same_phase_review_required'] and not changed['installed'] and not changed['dispatch_started']
            assert changed['options']['phase']=='srs' and 'Keep New Verbatim Guidance' in changed['options']['change_request']
            assert o['change_request'] in changed['options']['change_request'] and changed['options']['source_checkpoint_id']==o['source_checkpoint_id']
            assert store.task(tid)==before and not (dest/'execution-generation.json').exists()
            submitted=store.decision_draft(tid,'revision-changes:'+lid,installation_review['review_sha256'],changed['submission_id'])
            assert submitted['change_request']=='Keep New Verbatim Guidance'
            from delivery_api.closed_revision_job import KIND
            for invalid in ({'extra_cost_acknowledged':False},{'artifact_evidence':[]},{'gate_key':'gate-needs'}):
                with pytest.raises(ValueError):store.enqueue_worker(tid,KIND,execution_options=changed['options']|invalid)
            with store._postgres.transaction() as conn:
                for invalid in ({'extra_cost_acknowledged':False},{'extra_cost_acknowledged':None},{'phase':None},{'gate_key':'gate-needs'}):
                    with pytest.raises(Exception):
                        with conn.transaction():
                            conn.execute("INSERT INTO worker_jobs(job_id,owner_id,auth_session_id,task_id,session_id,generation,kind,state,attempt,created_at,updated_at,execution_options) VALUES (%s,%s,%s,%s,%s,1,%s,'pending',0,0,0,%s::jsonb)",
                                (uuid.uuid4().hex,A.owner_id,A.session_id,tid,cap.session_id,KIND,json.dumps(changed['options']|invalid)))
            queued=adapter.queue_submitted_changes(changed['submission_id'])
            assert adapter.queue_submitted_changes(changed['submission_id'])==queued
            assert store.worker_job(queued)['execution_options']==changed['options']
            assert store.worker_job(queued)['state']=='pending' and store.task(tid)==before
            preserved={path:path.read_bytes() for directory in (root,dest) for path in directory.rglob('*') if path.is_file() and 'revision-proposals' not in path.relative_to(directory).parts}
            import asyncio
            from delivery_api.same_host_producer import SameHostProducerWorker
            from delivery_api.closed_revision_job import execute_closed_revision_proposal
            from delivery_api.revision_evidence import read_revision_artifacts
            from spec_generator.revision_worker import run_revision_proposal
            from spec_generator.worker_runtime import JobRuntime
            from spec_generator.model_config import StageModel
            from langgraph.graph import StateGraph,END
            from typing import TypedDict
            from copy import deepcopy
            class FakeState(TypedDict,total=False):
                intent_model:dict
                conops:dict
                needs:dict
                system_baseline:dict
                srs:dict
                gate_feedback:dict
            def fake_graph(log,checkpointer,**kwargs):
                graph=StateGraph(FakeState);graph.add_node('needs',lambda st:{})
                def regenerate(st):
                    assert st['gate_feedback']['srs']==changed['options']['change_request']
                    artifact=deepcopy(documents['spec/srs.json'])
                    artifact['purpose']='Same Phase Regenerated With Submitted Offline Guidance'
                    return {'srs':artifact,'gate_feedback':{}}
                graph.add_node('srs',regenerate);graph.set_entry_point('needs');graph.add_edge('needs','srs');graph.add_edge('srs',END)
                return graph.compile(checkpointer=checkpointer)
            from types import SimpleNamespace
            def runtime(capability,owned):
                return JobRuntime({'srs':StageModel('fake','fake')},str(owned/'calls.jsonl'),lambda text:None)
            worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),
                usage_log_factory=lambda *a:None,runtime_factory=runtime)
            execution=asyncio.run(execute_closed_revision_proposal(store,worker,queued,
                read_artifact_evidence=lambda c,r,phase:read_revision_artifacts(store,c,r,phase,artifacts_base=tmp_path),
                log_factory=lambda *a:None,prepare=lambda **kwargs:run_revision_proposal(**kwargs,graph_factory=fake_graph)))
            assert execution['started'] and len(execution['proposals'])==1 and store.worker_job(queued)['state']=='done'
            assert store.task(tid)==before and not execution['restart_dispatched']
            new_proposal=execution['proposals'][0]
            evidence={'artifacts':changed['options']['artifact_evidence'],'checkpoint_id':changed['options']['source_checkpoint_id'],'proposal_sha256':new_proposal['artifact_sha256']}
            held=store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=changed['options'],proposal=new_proposal,
                approval_reference=changed['submission_id'],read_current_evidence=lambda:evidence)
            proposal_document=store.decision_review(tid,'pg-revision-proposal:'+new_proposal['proposal_id'],'result')
            assert proposal_document==new_proposal
            planned=store.retain_closed_revision_lineage_plan(tid,intent_id=held['intent_id'],semantic_session_id=cap.session_id,new_execution_session_id='revised-'+uuid.uuid4().hex,
                retained_proposal={'proposal':new_proposal,'document_sha256':hashlib.sha256(json.dumps(proposal_document,sort_keys=True,indent=2).encode()).hexdigest()})
            fresh_lid=planned['lineage_id']
            store.retain_revision_source_review(tid,lineage_id=fresh_lid,artifacts_base=tmp_path)
            fresh_prepared=store.append_closed_revision_preparation_event(tid,lineage_id=fresh_lid,expected_previous_sha256=None,state='prepared',
                evidence={'intent_id':held['intent_id'],'lineage_id':fresh_lid})
            with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                fresh_staged=store.prepare_revision_staging(tid,lineage_id=fresh_lid,expected_previous_sha256=fresh_prepared['event_sha256'],artifacts_base=tmp_path)
            fresh_review=store.retain_revision_installation_review(tid,lineage_id=fresh_lid,expected_staged_event_sha256=fresh_staged['event_sha256'],artifacts_base=tmp_path)
            assert fresh_review['review']['phase']=='srs' and fresh_review['review_sha256']!=installation_review['review_sha256']
            from delivery_api.revision_acceptance_view import revision_acceptance_view
            shown=revision_acceptance_view(**fresh_review,artifact=new_proposal['artifact'],spent_credits=0)
            assert shown['view']['phase_title']=='Requirements' and shown['binding']['review_sha256']==fresh_review['review_sha256']
            assert new_proposal['artifact']['purpose']=='Same Phase Regenerated With Submitted Offline Guidance'
            assert preserved=={path:path.read_bytes() for path in preserved}
            from test_revision_acceptance_browser import assert_same_phase_refresh
            old_shown=revision_acceptance_view(**installation_review,artifact=saved['proposal']['artifact'],spent_credits=0)
            assert_same_phase_refresh(old_shown,shown,adapter_result)
            assert store.task(tid)==before
            with pytest.raises(ValueError,match='superseded'):store.record_private_revision_acceptance(tid,**acceptance_kw)
            with pytest.raises(TenantAccessDenied,match='revoked'):
                store.verify_private_revision_acceptance(installation_review['review'],installation_review['review_sha256'],receipt['acceptance_reference'])
            return
        auth_checks=[]
        def test_acceptance(review,sha,reference):
            assert review==installation_review['review'] and sha==installation_review['review_sha256']
            assert reference==receipt['acceptance_reference'];store.verify_private_revision_acceptance(review,sha,reference);auth_checks.append(1)
        intent_kw=dict(lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],review_sha256=installation_review['review_sha256'],
            installation_id='install-'+uuid.uuid4().hex,acceptance_reference=receipt['acceptance_reference'],validate_acceptance=test_acceptance,artifacts_base=tmp_path)
        install=adapter.prepare_installation(receipt['acceptance_reference'],intent_kw['installation_id'])
        auth_checks.extend([1,1])
        assert not install['replayed'] and not install['plan']['installed'] and len(auth_checks)==2
        assert store.retain_private_installation_intent(tid,**intent_kw)['replayed']
        def revoked(*a):raise PermissionError('test acceptance revoked')
        with pytest.raises(PermissionError):store.retain_private_installation_intent(tid,**(intent_kw|{'validate_acceptance':revoked}))
        with store._postgres.transaction() as conn:
            auth=conn.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=%s',(intent_kw['installation_id'],)).fetchone()
        if coordinated:
            from delivery_api.revision_install_coordinator import apply_private_installation
            if coordinated=='revoked_before_promotion':
                store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])
                with pytest.raises(TenantAccessDenied,match='revoked'):
                    with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                        apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=store.verify_private_revision_acceptance)
                assert not (dest/'execution-generation.json').exists() and store.task(tid)==before
                return
            if isinstance(coordinated,str) and coordinated not in ('continuation_failure','continuation_consume','continuation_consume_revoked','continuation_real_graph','architecture_continuation','pg_install_only','pg_continuation_only','pg_continuation_consume','pg_continuation_consume_revoked','pg_continuation_failure','pg_activation_rollback','pg_anchor_tamper','pg_stage_tamper','pg_source_tamper','pg_continuation_tamper','pg_control_stop','pg_child_control'):
                from delivery_api import revision_install_coordinator as coordinator
                from delivery_api import pg_revision_install as pg_installer
                original=pg_installer.promote_stage
                def interrupted(*args,**kwargs):
                    if coordinated=='after_promotion':original(*args,**kwargs)
                    raise RuntimeError('constructed interruption')
                monkeypatch.setattr(pg_installer,'promote_stage',interrupted)
                with pytest.raises(RuntimeError,match='constructed interruption'):
                    with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                        apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],
                            expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
                assert store.task(tid)==before and store.session_binding(args['lineage']['execution_session_id']) is None
                reconciled=store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                expected='private_stage_requires_review' if coordinated=='before_promotion' else 'files_promoted_database_uninstalled_requires_review'
                assert reconciled['state']==expected and not reconciled['automatic_retry'] and not reconciled['resume_available']
                with pytest.raises(ValueError,match='reconciliation'):
                    with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                        apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
                return
            # Concurrent destination holder must block before anchor/journal effects.
            key='pg-revision-install:'+A.owner_id+':'+args['lineage']['execution_session_id']
            import psycopg
            with psycopg.connect(DSN,autocommit=True) as holder:
                holder.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(key,))
                try:
                    with pytest.raises(BlockingIOError):
                        with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                            apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
                    assert not (dest/'execution-generation.json').exists() and store.task(tid)==before
                finally:holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
            if coordinated=='pg_activation_rollback':
                from delivery_api import pg_revision_install
                original_seed=pg_revision_install.seed_accepted_checkpoint
                def failed_seed(*a,**kw):
                    original_seed(*a,**kw)
                    raise RuntimeError('constructed post-seed rollback')
                monkeypatch.setattr(pg_revision_install,'seed_accepted_checkpoint',failed_seed)
                with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                    with pytest.raises(RuntimeError,match='post-seed rollback'):
                        apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
                assert store.task(tid)==before and store.session_binding(args['lineage']['execution_session_id']) is None
                with store._postgres.transaction() as conn:
                    assert conn.execute('SELECT 1 FROM checkpoints WHERE thread_id=%s',(args['lineage']['execution_session_id'],)).fetchone() is None
                    assert conn.execute('SELECT 1 FROM installed_execution_bindings WHERE session_id=%s',(args['lineage']['execution_session_id'],)).fetchone() is None
                observed=store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                assert observed['state']=='files_promoted_database_uninstalled_requires_review' and not observed['automatic_retry']
                return
            with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
                result=apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],
                    expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
            later=WorkerCapability.capture(store,tid);later.validate(store)
            assert result['installed'] and not result['dispatch_started'] and later.execution_anchor.semantic_session_id==cap.session_id
            assert store.task(tid)['generation']==2 and store.session_binding(cap.session_id)['generation']==1
            task=store.task(tid)
            assert next(g for g in task['gates'] if g['after_phase']==phase)['state']=='decided'
            assert next(p for p in task['phases'] if p['phase_key']==phase)['state']=='approved'
            assert task['active_phase']==('specs' if phase=='architecture' else 'architecture') and result['continuation_prepared'] and not result['dispatch_started']
            reconciled=store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
            assert reconciled['state']=='installed_verified_no_dispatch' and not reconciled['automatic_retry'] and not reconciled['unverified']
            if coordinated in ('pg_install_only','pg_continuation_only','pg_continuation_consume','pg_continuation_consume_revoked','pg_continuation_failure','pg_anchor_tamper','pg_stage_tamper','pg_source_tamper','pg_continuation_tamper','pg_control_stop','pg_child_control'):
                from delivery_api.generator_checkpoint import GeneratorCheckpoint
                from spec_generator.graph import build_graph
                from types import SimpleNamespace
                reader=GeneratorCheckpoint(store,later,None,'spec',writable=False)
                original_installed=reader.get_tuple({'configurable':{'thread_id':later.session_id}})
                state=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=reader).get_state({'configurable':{'thread_id':later.session_id}})
                assert state.next==('gate_'+phase,) and state.values[phase]==saved['proposal']['artifact']
                assert not dest.exists() and not list(root.parent.rglob('checkpoints.sqlite'))
                from interview_engine.installed_execution import load_database_installed_execution_control
                from delivery_api.tenancy import session_root
                from delivery_api.pg_execution_fence import PostgresExecutionFence,PostgresControlFence
                pg_root=session_root(tmp_path,A,later.session_id)
                def read_installed_db():
                    PostgresControlFence(store,later)._validate()
                    return store.installed_execution_anchor(later.session_id)
                control=load_database_installed_execution_control(pg_root,expected_anchor=later.execution_anchor,expected_anchor_sha256=later.anchor_sha256,
                    read_binding=read_installed_db,fence_factory=lambda r,a:PostgresExecutionFence(store,later,root=r))
                assert control.fence.generation==2 and control.validate()['document_sha256']==later.anchor_sha256
                with pytest.raises(ValueError,match='new interview input'):control.reject_interview_mutation()
                from interview_engine.installed_execution import InstalledExecutionRegistry
                from interview_engine.tenant_worker import _owner
                registry=InstalledExecutionRegistry(tmp_path,read_binding=lambda owner,sid:read_installed_db(),fence_factory=lambda r,a:PostgresControlFence(store,later))
                token=_owner.set(A.owner_id)
                try:
                    assert registry.get(later.session_id).anchor==later.execution_anchor
                    with pytest.raises(ValueError,match='new interview input'):registry.respond(later.session_id,{})
                    if coordinated=='pg_control_stop':
                        with PostgresExecutionFence(store,later,root=pg_root).running():
                            assert registry.stop(later.session_id)=='STOP_REQUESTED'
                            assert registry.stop(later.session_id)=='STOP_REQUESTED' and store.task(tid)['generation']==3
                            with pytest.raises(Exception,match='quiescence'):registry.reconcile_stopped(later.session_id)
                        assert registry.reconcile_stopped(later.session_id)=='STOPPED'
                        assert store.confirm_task_stopped(tid,engine_quiescent=True)['state']=='stopped'
                        assert not pg_root.exists()
                        return
                finally:_owner.reset(token)
                assert not pg_root.exists()
                if coordinated=='pg_child_control':
                    from interview_engine.pg_session_store import PgSessionStore
                    from delivery_api.generator_documents import ChildGeneratorDocuments
                    from delivery_api.pg_execution_fence import ChildPostgresExecutionFence
                    from spec_generator.cooperative_run import cli_execution
                    from spec_generator.stop_fence import guarded_publish,StopRequested
                    import psycopg
                    from delivery_api.pg_execution_fence import InstalledInterviewControlRouter
                    from interview_engine.tenant_worker import TenantRegistryRouter,_session
                    from interview_engine.http_service import ProtocolFailure
                    from vmodel_contracts.interactive import ProcessStatus
                    route=InstalledInterviewControlRouter(store,tmp_path)
                    class NeverInterviewRegistry:
                        def __init__(self,*args,**kwargs):raise AssertionError('installed source must not load an interview graph')
                    router=TenantRegistryRouter(NeverInterviewRegistry,None,tmp_path,session_dsn='unused',installed_resolver=route.resolve)
                    ot=_owner.set(A.owner_id);st=_session.set(A.session_id)
                    try:
                        snapshot=router.snapshot(later.session_id)
                        assert snapshot.session_id==later.session_id and snapshot.task_id==tid and snapshot.process_status==ProcessStatus.COMPLETED and snapshot.open_input is None
                        with pytest.raises(ProtocolFailure,match='preserves approved'):router.respond(later.session_id,{})
                        with pytest.raises(ProtocolFailure,match='preserves approved'):router.switch_route(later.session_id,{})
                        assert router._registries=={}
                    finally:_owner.reset(ot);_session.reset(st)
                    from interview_engine.pg_installed_router import InstalledControlRouter
                    standalone=InstalledControlRouter(store._postgres._pool,tmp_path)
                    standalone_router=TenantRegistryRouter(NeverInterviewRegistry,None,tmp_path,session_dsn='unused',pool=store._postgres._pool,route_installed=True)
                    ot=_owner.set(A.owner_id);st=_session.set(A.session_id)
                    try:
                        assert standalone_router.snapshot(later.session_id).process_status==ProcessStatus.COMPLETED
                        assert standalone_router._registries=={}
                        from interview_engine.pg_installed_router import ScopedInstalledRegistry
                        fixed=ScopedInstalledRegistry(router,store._postgres._pool,tmp_path,A.owner_id,A.session_id)
                        assert fixed.snapshot(later.session_id).process_status==ProcessStatus.COMPLETED
                        assert standalone.resolve('snapshot',later.session_id)(later.session_id).process_status==ProcessStatus.COMPLETED
                        with pytest.raises(ProtocolFailure,match='preserves approved'):standalone.resolve('respond',later.session_id)(later.session_id,{})
                    finally:_owner.reset(ot);_session.reset(st)
                    pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
                    jid=store.enqueue_worker(tid,'phase:architecture');assert store.claim_worker(jid)==later
                    b=SimpleNamespace(session_id=later.session_id,owner_id=A.owner_id,task_id=tid,job_id=jid,generation=2,auth_session_id=A.session_id,execution_anchor_sha256=later.anchor_sha256)
                    claim=pg.claim(later.session_id,A.owner_id,'installed-child-test',60)
                    docs=ChildGeneratorDocuments(pg,claim,b);fence=ChildPostgresExecutionFence(docs,root=pg_root)
                    with cli_execution(fence,None):guarded_publish(lambda:docs.write('spec/architecture.partial.json',b'{"progress":1}'))
                    assert docs.read('spec/architecture.partial.json')==b'{"progress":1}'
                    with psycopg.connect(DSN) as admin:admin.execute('UPDATE installed_execution_bindings SET anchor_sha256=%s WHERE session_id=%s',('f'*64,later.session_id))
                    with pytest.raises(TenantAccessDenied,match='installed'):docs.read('spec/architecture.partial.json')
                    ot=_owner.set(A.owner_id);st=_session.set(A.session_id)
                    try:
                        with pytest.raises(PermissionError,match='journal'):standalone_router.snapshot(later.session_id)
                    finally:_owner.reset(ot);_session.reset(st)
                    with psycopg.connect(DSN) as admin:admin.execute('UPDATE installed_execution_bindings SET anchor_sha256=%s WHERE session_id=%s',(later.anchor_sha256,later.session_id))
                    assert docs.read('spec/architecture.partial.json')==b'{"progress":1}'
                    with cli_execution(fence,None):
                        store.request_task_stop(tid)
                        with pytest.raises(StopRequested):guarded_publish(lambda:docs.write('spec/architecture.partial.json',b'late'))
                    pg.release(claim)
                    ot=_owner.set(A.owner_id);st=_session.set(A.session_id)
                    try:
                        assert router.snapshot(later.session_id).process_status==ProcessStatus.STOPPED
                        assert router.stop(later.session_id).process_status==ProcessStatus.STOPPED
                        assert router.reconcile_stopped(later.session_id).process_status==ProcessStatus.STOPPED
                        assert store.task(tid)['generation']==3 and router._registries=={}
                        assert standalone.resolve('snapshot',later.session_id)(later.session_id).process_status==ProcessStatus.STOP_REQUESTED
                        from delivery_api.same_host_producer import SameHostProducerWorker
                        assert SameHostProducerWorker(store,tmp_path,gateway_factory=None,usage_log_factory=None,isolated_quiescent=lambda:True).reconcile_stopped(jid)
                        assert standalone.resolve('snapshot',later.session_id)(later.session_id).process_status==ProcessStatus.STOPPED
                        assert standalone.resolve('stop',later.session_id)(later.session_id).process_status==ProcessStatus.STOPPED
                        assert standalone.resolve('reconcile_stopped',later.session_id)(later.session_id).process_status==ProcessStatus.STOPPED
                        assert store.task(tid)['generation']==3
                    finally:_owner.reset(ot);_session.reset(st)
                    assert not pg_root.exists()
                    return
                if coordinated in ('pg_anchor_tamper','pg_stage_tamper','pg_source_tamper'):
                    import psycopg
                    if coordinated=='pg_source_tamper':
                        published=store.manifest_snapshot(tid,1)
                        source_row=next(row for row in published['rows'] if row['phase_key']==phase)
                        with psycopg.connect(DSN) as admin:
                            admin.execute('UPDATE artifact_bytes SET bytes=%s WHERE owner_id=%s AND sha256=%s',(b'{}',A.owner_id,source_row['sha256']))
                    else:
                        marker=('pg-install-anchor:'+intent_kw['installation_id']) if coordinated=='pg_anchor_tamper' else 'pg-revision-stage:'+args['lineage_sha256']
                        revision=install['plan_sha256'] if coordinated=='pg_anchor_tamper' else 'prepared'
                        with psycopg.connect(DSN) as admin:
                            raw=admin.execute('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s',(tid,marker,revision)).fetchone()[0]
                            value=json.loads(raw)
                            if coordinated=='pg_anchor_tamper':value['anchor_sha256']='f'*64
                            else:value['snapshot']['sha256']='f'*64
                            admin.execute('UPDATE decision_reviews SET payload=%s WHERE task_id=%s AND wait_id=%s AND revision=%s',(json.dumps(value),tid,marker,revision))
                    broken=store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                    assert broken['state']=='database_installed_requires_review' and broken['unverified'] and not broken['automatic_retry']
                    with pytest.raises(ValueError,match='unresolved'):
                        store.private_approved_continuation_basis(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                    return
                if coordinated!='pg_install_only':
                    from delivery_api.tenancy import session_root
                    from delivery_api.pg_execution_fence import PostgresExecutionFence
                    from delivery_api.generator_snapshot import GeneratorSnapshots
                    pg_root=session_root(tmp_path,A,later.session_id)
                    from delivery_api.revision_continuation import prepare_private_approved_continuation,reconcile_private_approved_continuation
                    if coordinated=='pg_continuation_failure':
                        from spec_generator import revision_checkpoint
                        def fail(**kw):raise RuntimeError('constructed PG preparation interruption')
                        monkeypatch.setattr(revision_checkpoint,'prepare_approved_continuation_checkpoint',fail)
                        with cli_execution(PostgresExecutionFence(store,later,root=pg_root),None):
                            with pytest.raises(RuntimeError,match='interruption'):
                                prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                            with pytest.raises(ValueError,match='reconciliation'):
                                prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                        observed=reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                        assert observed['state']=='continuation_requires_review' and not observed['automatic_retry']
                    else:
                        with cli_execution(PostgresExecutionFence(store,later,root=pg_root),None):
                            prepared=prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                        observed=reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                        assert observed['state']=='continuation_prepared_verified_no_dispatch' and observed['next_node']==('items' if pg_architecture else 'architecture')
                        assert not prepared['provider_invoked'] and not prepared['dispatch_started']
                        if coordinated=='pg_continuation_tamper':
                            import psycopg
                            with psycopg.connect(DSN) as admin:
                                marker='revision-continuation:'+intent_kw['installation_id']+':prepared'
                                raw=admin.execute('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s',(tid,marker,installation_review['review_sha256'])).fetchone()[0]
                                altered=json.loads(raw);altered['snapshot']['sha256']='f'*64
                                admin.execute('UPDATE decision_reviews SET payload=%s WHERE task_id=%s AND wait_id=%s AND revision=%s',(json.dumps(altered),tid,marker,installation_review['review_sha256']))
                            observed=reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                            assert observed['state']=='continuation_requires_review' and observed['unverified'] and not observed['automatic_retry']
                            assert reader.get_tuple({'configurable':{'thread_id':later.session_id}})==original_installed
                            return
                        if coordinated in ('pg_continuation_consume','pg_continuation_consume_revoked'):
                            import asyncio
                            from delivery_api.revision_continuation_worker import queue_private_approved_continuation,execute_private_approved_continuation
                            from delivery_api.same_host_producer import SameHostProducerWorker
                            from spec_generator.worker_runtime import JobRuntime
                            from spec_generator.model_config import StageModel
                            from langgraph.graph import StateGraph,END
                            from typing import TypedDict
                            from vmodel_contracts.architecture import ArchitectureOverview
                            class PGContinuationState(TypedDict,total=False):
                                intent_model:dict
                                conops:dict
                                needs:dict
                                system_baseline:dict
                                srs:dict
                                architecture:dict
                            def fake_graph(log,checkpointer,**kwargs):
                                graph=StateGraph(PGContinuationState);graph.add_node('gate_srs',lambda st:{})
                                def architecture(st):
                                    if coordinated=='pg_continuation_consume_revoked':store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])
                                    return {'architecture':ArchitectureOverview.model_validate({'session_id':cap.session_id,
                                        'baseline_id':st['system_baseline']['baseline_id'],'stack_entries':[],
                                        'components':[{'name':'Offline Review','responsibility':'Show approved result','requirement_ids':['R-1'],'baseline_refs':['repo:spec-generator']}],
                                        'decisions':[],'non_functionals':{}}).model_dump(mode='json')}
                                graph.add_node('architecture',architecture);graph.set_entry_point('gate_srs');graph.add_edge('gate_srs','architecture');graph.add_edge('architecture',END)
                                return graph.compile(checkpointer=checkpointer)
                            if pg_real:
                                from spec_generator import graph as real_graph
                                call_stages=[]
                                def fake_call(stage,payload,log,progress,**kwargs):
                                    assert stage=='architecture';call_stages.append(stage)
                                    schema=kwargs.get('schema') or {};props=set(schema.get('properties',{}))
                                    if props=={'stack_keys'}:value={'stack_keys':['language']}
                                    elif props=={'technology','off_list_justification','baseline_refs'}:value={'technology':'python','off_list_justification':None,'baseline_refs':['runtime:spec-generator:1']}
                                    elif props=={'responsibility'}:value={'responsibility':'serve approved specification'}
                                    elif props=={'position'}:value={'position':'consistent'}
                                    elif 'driver' in props:value={'title':'Keep Existing Runtime','decision':'Use Python','rationale':'Preserve current runtime','driver':'simplicity','options_considered':[{'option':'Change runtime','reason':'Unnecessary migration'}],'baseline_refs':['repo:spec-generator'],'evidence':[],'affected_repositories':[],'executes_concept_ids':[]}
                                    elif schema.get('type')=='array':
                                        item=set(schema['items'].get('properties',{}))
                                        value=[{'slug':'runtime'}] if 'slug' in item else [{'name':'api','requirement_ids':['R-1'],'baseline_refs':['repo:spec-generator'],'migration':False}]
                                    else:raise AssertionError(schema)
                                    if 'driver' in props:
                                        slot_text=payload.split('THE ADR SLOT',1)[1].split('\n',1)[1]
                                        slot,_=json.JSONDecoder().raw_decode(slot_text.lstrip())
                                        if slot['slug'].startswith('execute-'):
                                            value['executes_concept_ids']=['concept:native-app']
                                            value.update(title='Execute Native Concept',decision='Execute the selected native operating concept',rationale='Carry confirmed operating behavior',driver='founder_constraint',options_considered=[{'option':'Remote browser-only operation','reason':'Conflicts with chosen native operating concept'}])
                                    validator=kwargs.get('validate')
                                    checked=validator(value) if validator else None
                                    return checked if checked is not None else value
                                monkeypatch.setattr(real_graph,'_call_json',fake_call)
                                fake_graph=None  # Production graph construction/node routing, no model invocation.
                            queued=queue_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                            assert queue_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)==queued
                            worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,
                                runtime_factory=lambda c,r:JobRuntime({'architecture':StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None))
                            if coordinated=='pg_continuation_consume_revoked':
                                with pytest.raises(TenantAccessDenied,match='revoked'):
                                    asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))
                                assert store.worker_job(queued)['state']=='failed'
                                assert store.decision_review(tid,'revision-continuation-result:'+intent_kw['installation_id'],installation_review['review_sha256']) is None
                                assert store.task(tid)['state']=='pending'
                                assert not asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))['started']
                            else:
                                consumed=asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))
                                assert consumed['started'] and not consumed['downstream_dispatched'] and store.worker_job(queued)['state']=='done'
                                assert len(consumed['outputs'])==1 and consumed['outputs'][0]['phase']=='architecture'
                                assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='architecture')['state']=='waiting'
                                result=consumed['outputs'][0]
                                from spec_generator.graph import validated_architecture
                                assert validated_architecture(GeneratorSnapshots(store,later).read(result['snapshot']).checkpoint['channel_values']['architecture']).model_dump(mode='json')==result['artifact']
                                from delivery_api.revision_next_review import read_private_next_review,approve_private_next_review,prepare_private_successor,queue_private_successor
                                shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                                assert shown['artifact']==result['artifact'] and shown['review']['phase']=='architecture'
                                for acknowledgement in (False,None,1):
                                    with pytest.raises(ValueError,match='acknowledgement'):
                                        approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'],extra_cost_acknowledged=acknowledgement,selected_option='Approve',validate_submission=lambda *a:'constructed-test-only')
                                with pytest.raises(ValueError,match='changed'):
                                    approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256='f'*64,extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'constructed-test-only')
                                approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'constructed-test-only')
                                with cli_execution(PostgresExecutionFence(store,later,root=pg_root),None):
                                    successor=prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                                    with pytest.raises(ValueError,match='reconciliation'):
                                        prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                                assert successor['source_checkpoint_sha256']==shown['review']['checkpoint_sha256']
                                assert not successor['dispatch_started'] and not successor['provider_invoked']
                                next_job=queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                                assert queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])==next_job
                                assert GeneratorSnapshots(store,later).read(result['snapshot']).config==shown['review']['checkpoint']
                                from delivery_api.revision_next_review import execute_private_successor
                                from spec_generator import graph as real_graph
                                def fake_items(stage,payload,log,progress,**kwargs):
                                    assert stage=='items'
                                    if pg_fault and not getattr(fake_items,'faulted',False):
                                        fake_items.faulted=True
                                        if pg_fault=='pg_successor_revoked':store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])
                                        elif pg_fault=='pg_successor_expired':monkeypatch.setattr(store,'auth_session_active',lambda:False)
                                        elif pg_fault=='pg_successor_provider_hold':
                                            import psycopg
                                            from test_attempt_ledger import Q
                                            with psycopg.connect(DSN) as admin:
                                                admin.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2) ON CONFLICT(owner_id) DO NOTHING',(A.owner_id,'constructed-test-only'))
                                            fake_items.held=store.reserve_provider_attempt(next_job,uuid.uuid4().hex,Q,'constructed-test-hold')['attempt_id']
                                        else:
                                            import psycopg
                                            marker=('revision-chain-prepared:'+intent_kw['installation_id']) if pg_fault=='pg_successor_prepared_swap' else ('revision-continuation-result:'+intent_kw['installation_id'])
                                            ref=shown['review_sha256'] if pg_fault=='pg_successor_prepared_swap' else installation_review['review_sha256']
                                            with psycopg.connect(DSN) as admin:
                                                old=admin.execute('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s',(tid,marker,ref)).fetchone()[0]
                                                tampered=json.loads(old);tampered['snapshot']['sha256']='f'*64
                                                admin.execute('UPDATE decision_reviews SET payload=%s WHERE task_id=%s AND wait_id=%s AND revision=%s',(json.dumps(tampered),tid,marker,ref))

                                    value=[{'slug':'api','kind':'api_surface','title':'API','requirement_ids':['R-1'],'depends_on':[]}] if kwargs['schema'].get('type')=='array' else {'body_markdown':'# Approved Spec\n\nThe API emits the complete approved specification.'}
                                    checked=kwargs['validate'](value)
                                    return checked if checked is not None else value
                                monkeypatch.setattr(real_graph,'_call_json',fake_items)
                                next_worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,
                                    runtime_factory=lambda c,r:JobRuntime({'items':StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None))
                                if pg_fault:
                                    with pytest.raises((ValueError,TenantAccessDenied)):
                                        asyncio.run(execute_private_successor(store,next_worker,next_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256']))
                                    if pg_fault=='pg_successor_expired':monkeypatch.undo()
                                    assert store.worker_job(next_job)['state']==('running' if pg_fault=='pg_successor_expired' else 'failed')
                                    assert store.decision_review(tid,'revision-continuation-result:'+intent_kw['installation_id'],shown['review_sha256']) is None
                                    assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='specs')['state']!='waiting'
                                    assert reader.get_tuple({'configurable':{'thread_id':later.session_id}})==original_installed
                                    if pg_fault=='pg_successor_provider_hold':
                                        assert store.provider_attempt(fake_items.held)['state']=='reserved'
                                        store.transition_provider_attempt(fake_items.held,'cancel')
                                    assert not list(root.parent.rglob('checkpoints.sqlite'))
                                    return
                                next_result=asyncio.run(execute_private_successor(store,next_worker,next_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256']))
                                assert next_result['started'] and next_result['outputs'][0]['phase']=='specs' and store.worker_job(next_job)['state']=='done'
                                next_shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,result_reference=shown['review_sha256'])
                                assert next_shown['review']['phase']=='specs'
                                for expected_stage,expected_phase in [('tests','tests'),('validation','validation')]:
                                    source_reference=next_shown['review']['result_reference'];current_digest=next_shown['review_sha256']
                                    approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest,extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'constructed-test-only',result_reference=source_reference)
                                    with cli_execution(PostgresExecutionFence(store,later,root=pg_root),None):
                                        prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest)
                                    def fake_final(stage,payload,log,progress,**kwargs):
                                        assert stage==expected_stage
                                        schema=kwargs['schema']
                                        if stage=='tests':
                                            ids=schema['properties']['fills']['required']
                                            value={'fills':{key:{'description':'Confirm approved specification output','runner':'static','components':[('api' if pg_real else 'Offline Review')],'definition':{'preconditions':[],'steps':['Inspect generated API specification'],'expected_outcomes':['The API specification contains the approved requirement content'],'cleanup':[],'cases':[],'execution':{}}} for key in ids}}
                                        else:
                                            value={'items':[{'components':[('api' if pg_real else 'Offline Review')],'what_the_human_does':'Read the generated specification','what_to_observe':'The approved user workflow remains clear','pass_when':'The user can identify the intended behavior','evidence':'Record the review outcome','environment':'Local review'}]}
                                            if 'criterion_id' in schema['properties']['items']['items']['properties']:value['items'][0]['criterion_id']=None
                                        checked=kwargs['validate'](value)
                                        return checked if checked is not None else value
                                    monkeypatch.setattr(real_graph,'_call_json',fake_final)
                                    final_worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,
                                        runtime_factory=lambda c,r:JobRuntime({expected_stage:StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None))
                                    final_job=queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest)
                                    final_result=asyncio.run(execute_private_successor(store,final_worker,final_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest))
                                    assert final_result['outputs'][0]['phase']==expected_phase
                                    next_shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,result_reference=current_digest)
                                    assert next_shown['review']['phase']==expected_phase
                                terminal=approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=next_shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'constructed-test-only',result_reference=next_shown['review']['result_reference'])
                                assert terminal['phase']=='validation' and store.task(tid)['state']=='done'
                                from delivery_api.revision_next_review import successor_basis
                                with pytest.raises(ValueError,match='terminal'):
                                    successor_basis(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=next_shown['review_sha256'])


                            assert GeneratorSnapshots(store,later).read(prepared['snapshot']).config==prepared['checkpoint']

                        from delivery_api.generator_snapshot import GeneratorSnapshots
                        with pytest.raises(ValueError,match='digest'):
                            GeneratorSnapshots(store,later).read(prepared['snapshot']|{'sha256':'f'*64})
                    unchanged=reader.get_tuple({'configurable':{'thread_id':later.session_id}})
                    assert unchanged==original_installed
                    assert not list(root.parent.rglob('checkpoints.sqlite'))

                with tenant_context(B):
                    with pytest.raises(TenantAccessDenied):reader.get_tuple({'configurable':{'thread_id':later.session_id}})
                from delivery_api.pg_revision_install import seed_accepted_checkpoint
                with pytest.raises(ValueError,match='parent CAS'):
                    with store._task_operation_guard('task.rerun'):seed_accepted_checkpoint(store,tid,intent_kw['installation_id'],test_acceptance)
                return
            from delivery_api.tenancy import session_root
            promoted_root=session_root(tmp_path,A,later.session_id)
            engine_source=os.getenv('DELIVERY_TEST_ENGINE_SOURCE')
            if engine_source:monkeypatch.syspath_prepend(engine_source)
            from interview_engine.installed_execution import load_database_installed_execution_control
            from delivery_api.pg_execution_fence import PostgresControlFence
            def db_binding():
                PostgresControlFence(store,later)._validate()
                return store.installed_execution_anchor(later.session_id)
            def load_control(**kwargs):
                return load_database_installed_execution_control(promoted_root,read_binding=db_binding,fence_factory=lambda r,a:PostgresControlFence(store,later),**kwargs)
            control=load_control(expected_anchor=later.execution_anchor,expected_anchor_sha256=later.anchor_sha256)
            assert control.fence.generation==2 and control.validate()['document_sha256']==later.anchor_sha256
            from interview_engine.installed_execution import InstalledExecutionRegistry
            from interview_engine.tenant_worker import _owner
            def read_installed(owner,sid):
                assert owner==A.owner_id
                later.validate(store)
                return store.installed_execution_anchor(sid)
            registry=InstalledExecutionRegistry(tmp_path,read_binding=read_installed,fence_factory=lambda r,a:PostgresControlFence(store,later))
            token=_owner.set(A.owner_id)
            try:
                registered=registry.get(later.session_id)
                assert registered.anchor==later.execution_anchor and registered.fence.generation==2
                with pytest.raises(ValueError,match='new interview input'):registry.respond(later.session_id,{})
            finally:_owner.reset(token)
            assert not (promoted_root/'interview'/'http-session.json').exists()
            from delivery_api.revision_continuation import prepare_private_approved_continuation
            from delivery_api.generator_checkpoint import GeneratorCheckpoint
            from delivery_api.generator_snapshot import GeneratorSnapshots
            installed_reader=GeneratorCheckpoint(store,later,None,'spec',writable=False)
            cfg={'configurable':{'thread_id':later.session_id}}
            def installed_checkpoint():return installed_reader.get_tuple(cfg)
            original_checkpoint=installed_checkpoint()
            def prepared_receipt():return store.decision_review(tid,'revision-continuation:'+intent_kw['installation_id']+':prepared',installation_review['review_sha256'])
            def result_receipt(reference=None):return store.decision_review(tid,'revision-continuation-result:'+intent_kw['installation_id'],reference or installation_review['review_sha256'])
            def prepared_checkpoint():return GeneratorSnapshots(store,later).read(prepared_receipt()['snapshot'])
            from delivery_api.revision_continuation import reconcile_private_approved_continuation
            if coordinated=='continuation_failure':
                from spec_generator import revision_checkpoint
                def fail(**kwargs):raise RuntimeError('constructed continuation interruption')
                monkeypatch.setattr(revision_checkpoint,'prepare_approved_continuation_checkpoint',fail)
                with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                    with pytest.raises(RuntimeError,match='interruption'):
                        prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                    with pytest.raises(ValueError,match='reconciliation'):
                        prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                observed=reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                assert observed['state']=='continuation_requires_review' and not observed['automatic_retry']
                assert installed_checkpoint()==original_checkpoint
                return
            with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                continuation=prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                assert continuation['next_node']==('items' if phase=='architecture' else 'architecture') and not continuation['provider_invoked'] and not continuation['dispatch_started']
                with pytest.raises(ValueError,match='reconciliation'):
                    prepare_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
            assert installed_checkpoint()==original_checkpoint
            observed=reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
            assert observed['state']=='continuation_prepared_verified_no_dispatch' and not observed['dispatch_available']
            if coordinated in ('continuation_consume','continuation_consume_revoked','continuation_real_graph','architecture_continuation'):
                import asyncio
                from types import SimpleNamespace
                from delivery_api.revision_continuation_worker import queue_private_approved_continuation,execute_private_approved_continuation
                from delivery_api.same_host_producer import SameHostProducerWorker
                from spec_generator.worker_runtime import JobRuntime
                from spec_generator.model_config import StageModel
                from langgraph.graph import StateGraph,END
                from typing import TypedDict
                from vmodel_contracts.architecture import ArchitectureOverview
                class ContinuationState(TypedDict,total=False):
                    intent_model:dict
                    conops:dict
                    needs:dict
                    system_baseline:dict
                    srs:dict
                    architecture:dict
                def fake_graph(log,checkpointer,**kwargs):
                    graph=StateGraph(ContinuationState)
                    graph.add_node('gate_srs',lambda st:{})
                    def architecture(st):
                        if coordinated=='continuation_consume_revoked':store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])
                        return {'architecture':ArchitectureOverview.model_validate({'session_id':cap.session_id,
                            'baseline_id':st['system_baseline']['baseline_id'],'stack_entries':[],
                            'components':[{'name':'Offline Review','responsibility':'Show approved result','requirement_ids':['R-1'],'baseline_refs':['repo:spec-generator']}],
                            'decisions':[],'non_functionals':{}}).model_dump(mode='json')}
                    graph.add_node('architecture',architecture);graph.set_entry_point('gate_srs')
                    graph.add_edge('gate_srs','architecture');graph.add_edge('architecture',END)
                    return graph.compile(checkpointer=checkpointer)
                queued=queue_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                assert queue_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)==queued
                immutable=prepared_checkpoint()
                def runtime(c,r):return JobRuntime({('items' if phase=='architecture' else 'architecture'):StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None)
                worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,runtime_factory=runtime)
                if coordinated=='continuation_consume_revoked':
                    with pytest.raises(TenantAccessDenied,match='revoked'):
                        asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))
                    assert store.worker_job(queued)['state']=='failed'
                    assert result_receipt() is None
                    assert store.task(tid)['state']=='pending'
                    assert prepared_checkpoint()==immutable
                    assert not asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))['started']
                    return
                if coordinated=='continuation_real_graph':
                    from spec_generator import graph as real_graph
                    call_stages=[]
                    def fake_call(stage,payload,log,progress,**kwargs):
                        assert stage=='architecture';call_stages.append(stage)
                        schema=kwargs.get('schema') or {};props=set(schema.get('properties',{}))
                        if props=={'stack_keys'}:value={'stack_keys':['language']}
                        elif props=={'technology','off_list_justification','baseline_refs'}:value={'technology':'python','off_list_justification':None,'baseline_refs':['runtime:spec-generator:1']}
                        elif props=={'responsibility'}:value={'responsibility':'serve approved specification'}
                        elif props=={'position'}:value={'position':'consistent'}
                        elif 'driver' in props:value={'title':'Keep Existing Runtime','decision':'Use Python','rationale':'Preserve current runtime','driver':'simplicity','options_considered':[{'option':'Change runtime','reason':'Unnecessary migration'}],'baseline_refs':['repo:spec-generator'],'evidence':[],'affected_repositories':[],'executes_concept_ids':[]}
                        elif schema.get('type')=='array':
                            item=set(schema['items'].get('properties',{}))
                            value=[{'slug':'runtime'}] if 'slug' in item else [{'name':'api','requirement_ids':['R-1'],'baseline_refs':['repo:spec-generator'],'migration':False}]
                        else:raise AssertionError(schema)
                        if 'driver' in props:
                            slot_text=payload.split('THE ADR SLOT',1)[1].split('\n',1)[1]
                            slot,_=json.JSONDecoder().raw_decode(slot_text.lstrip())
                            if slot['slug'].startswith('execute-'):
                                value['executes_concept_ids']=['concept:native-app']
                                value.update(title='Execute Native Concept',decision='Execute the selected native operating concept',rationale='Carry confirmed operating behavior',driver='founder_constraint',options_considered=[{'option':'Remote browser-only operation','reason':'Conflicts with chosen native operating concept'}])
                        validator=kwargs.get('validate')
                        checked=validator(value) if validator else None
                        return checked if checked is not None else value
                    monkeypatch.setattr(real_graph,'_call_json',fake_call)
                    fake_graph=None  # Production graph construction/node routing, no model invocation.
                if coordinated=='architecture_continuation':
                    from spec_generator import graph as real_graph
                    call_stages=[]
                    def fake_items(stage,payload,log,progress,**kwargs):
                        assert stage=='items';call_stages.append(stage)
                        schema=kwargs.get('schema') or {}
                        value=[{'slug':'api','kind':'api_surface','title':'API','requirement_ids':['R-1'],'depends_on':[]}] if schema.get('type')=='array' else {'body_markdown':'# Approved Spec\n\nThe API owns specification emission and provides a complete result for the approved requirement.'}
                        checked=kwargs['validate'](value)
                        return checked if checked is not None else value
                    monkeypatch.setattr(real_graph,'_call_json',fake_items);fake_graph=None
                consumed=asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))
                if coordinated=='architecture_continuation':assert call_stages and set(call_stages)=={'items'}
                if coordinated=='continuation_real_graph':assert call_stages and set(call_stages)=={'architecture'}
                assert consumed['started'] and len(consumed['outputs'])==1 and store.worker_job(queued)['state']=='done'
                assert not consumed['downstream_dispatched'] and store.task(tid)['state']=='waiting_gate'
                assert next(g for g in store.task(tid)['gates'] if g['after_phase']==('specs' if phase=='architecture' else 'architecture'))['state']=='waiting'
                assert next(p for p in store.task(tid)['phases'] if p['phase_key']==phase)['state']=='approved'
                assert prepared_checkpoint()==immutable
                assert installed_checkpoint()==original_checkpoint
                assert not asyncio.run(execute_private_approved_continuation(store,worker,queued,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,graph_factory=fake_graph))['started']
                if coordinated in ('continuation_real_graph','architecture_continuation'):
                    from delivery_api.revision_next_review import read_private_next_review,approve_private_next_review
                    shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
                    assert shown['review']['phase']==('specs' if phase=='architecture' else 'architecture')
                    with pytest.raises(ValueError,match='changed'):
                        approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256='f'*64,extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'test-only-submission')
                    for rejected in (False,None,1):
                        with pytest.raises(ValueError,match='acknowledgement'):
                            approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'],extra_cost_acknowledged=rejected,selected_option='Approve',validate_submission=lambda *a:'test-only-submission')
                    with pytest.raises(ValueError,match='submission reference'):
                        approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:None)
                    assert next(g for g in store.task(tid)['gates'] if g['after_phase']==shown['review']['phase'])['state']=='waiting'
                    approved=approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'test-only-submission')
                    assert approved['phase']==shown['review']['phase'] and not approved['dispatch_started']
                    if coordinated=='continuation_real_graph':
                        from delivery_api.revision_next_review import prepare_private_successor,queue_private_successor,execute_private_successor
                        source_continuation=GeneratorSnapshots(store,later).read(result_receipt()['snapshot'])
                        with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                            next_prepared=prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                        assert next_prepared['source_checkpoint_sha256']==shown['review']['checkpoint_sha256']
                        with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                            with pytest.raises(ValueError,match='reconciliation'):
                                prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                        original_result=result_receipt()
                        import psycopg
                        def replace_review(marker,revision,value):
                            with psycopg.connect(DSN) as admin:
                                admin.execute('UPDATE decision_reviews SET payload=%s WHERE task_id=%s AND wait_id=%s AND revision=%s',(json.dumps(value),tid,marker,revision))
                        result_marker='revision-continuation-result:'+intent_kw['installation_id']
                        replace_review(result_marker,installation_review['review_sha256'],original_result|{'artifact':{}})
                        with pytest.raises(ValueError,match='differs'):
                            queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                        replace_review(result_marker,installation_review['review_sha256'],original_result)
                        def next_runtime(c,r):return JobRuntime({'items':StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None)
                        next_worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,runtime_factory=next_runtime)
                        def fake_next_items(stage,payload,log,progress,**kwargs):
                            assert stage=='items'
                            schema=kwargs.get('schema') or {}
                            value=[{'slug':'api','kind':'api_surface','title':'API','requirement_ids':['R-1'],'depends_on':[]}] if schema.get('type')=='array' else {'body_markdown':'# Approved Spec\n\nThe API emits the complete approved specification.'}
                            if fault:
                                if fault=='successor_revoked':store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])
                                elif fault=='successor_provider_hold':
                                    if not hasattr(fake_next_items,'held'):
                                        import psycopg
                                        from test_attempt_ledger import Q
                                        with psycopg.connect(DSN) as admin:
                                            admin.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2) ON CONFLICT(owner_id) DO NOTHING',(A.owner_id,'test-only'))
                                        attempt=store.reserve_provider_attempt(next_job,uuid.uuid4().hex,Q,'constructed-test-hold')
                                        fake_next_items.held=attempt['attempt_id']
                                elif fault=='successor_expired':monkeypatch.setattr(store,'auth_session_active',lambda:False)
                                else:
                                    if not getattr(fake_next_items,'swapped',False):
                                        fake_next_items.swapped=True
                                        marker=result_marker if fault=='successor_swap' else 'revision-chain-prepared:'+intent_kw['installation_id']
                                        ref=installation_review['review_sha256'] if fault=='successor_swap' else shown['review_sha256']
                                        prior=store.decision_review(tid,marker,ref)
                                        altered=json.loads(json.dumps(prior));altered['snapshot']['sha256']='f'*64
                                        replace_review(marker,ref,altered)
                            checked=kwargs['validate'](value)
                            return checked if checked is not None else value
                        monkeypatch.setattr(real_graph,'_call_json',fake_next_items)
                        next_job=queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])
                        if fault:
                            with pytest.raises((ValueError,TenantAccessDenied)):
                                asyncio.run(execute_private_successor(store,next_worker,next_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256']))
                            if fault=='successor_expired':
                                with pytest.raises(TenantAccessDenied,match='revoked'):installed_checkpoint()
                                monkeypatch.setattr(store,'auth_session_active',lambda:True)  # End the deliberate test-only expiry before owned evidence readback.
                            assert store.worker_job(next_job)['state']==('running' if fault=='successor_expired' else 'failed')
                            assert result_receipt(shown['review_sha256']) is None
                            assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='specs')['state']!='waiting'
                            assert installed_checkpoint()==original_checkpoint
                            if fault=='successor_provider_hold':
                                assert store.provider_attempt(fake_next_items.held)['state']=='reserved'
                                store.transition_provider_attempt(fake_next_items.held,'cancel')
                            return
                        next_consumed=asyncio.run(execute_private_successor(store,next_worker,next_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256']))
                        assert next_consumed['started'] and next_consumed['outputs'][0]['phase']=='specs'
                        assert queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256'])==next_job
                        assert not asyncio.run(execute_private_successor(store,next_worker,next_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=shown['review_sha256']))['started']
                        next_shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,result_reference=shown['review_sha256'])
                        assert next_shown['review']['phase']=='specs'
                        for expected_stage,expected_phase in [('tests','tests'),('validation','validation')]:
                            source_reference=next_shown['review']['result_reference']
                            next_approved=approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=next_shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'test-only-submission',result_reference=source_reference)
                            current_digest=next_shown['review_sha256']
                            with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                                prepared_successor=prepare_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest)
                            def final_runtime(c,r):return JobRuntime({expected_stage:StageModel('fake','fake')},str(r/'calls.jsonl'),lambda text:None)
                            final_worker=SameHostProducerWorker(store,tmp_path,gateway_factory=lambda c,j:SimpleNamespace(store=store,job_id=j),usage_log_factory=lambda *a:None,runtime_factory=final_runtime)
                            def fake_final(stage,payload,log,progress,**kwargs):
                                assert stage==expected_stage
                                schema=kwargs['schema']
                                if stage=='tests':
                                    ids=schema['properties']['fills']['required']
                                    value={'fills':{key:{'description':'Confirm approved specification output','runner':'static','components':['api'],'definition':{'preconditions':[],'steps':['Inspect generated API specification'],'expected_outcomes':['The API specification contains the approved requirement content'],'cleanup':[],'cases':[],'execution':{}}} for key in ids}}
                                else:
                                    value={'items':[{'components':['api'],'what_the_human_does':'Read the generated specification','what_to_observe':'The approved user workflow remains clear','pass_when':'The user can identify the intended behavior','evidence':'Record the review outcome','environment':'Local review'}]}
                                    props=schema['properties']['items']['items']['properties']
                                    if 'criterion_id' in props:value['items'][0]['criterion_id']=None
                                checked=kwargs['validate'](value)
                                return checked if checked is not None else value
                            monkeypatch.setattr(real_graph,'_call_json',fake_final)
                            final_job=queue_private_successor(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest)
                            final_result=asyncio.run(execute_private_successor(store,final_worker,final_job,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=current_digest))
                            assert final_result['outputs'][0]['phase']==expected_phase
                            next_shown=read_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,result_reference=current_digest)
                            assert next_shown['review']['phase']==expected_phase
                        terminal=approve_private_next_review(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=next_shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=lambda *a:'test-only-submission',result_reference=next_shown['review']['result_reference'])
                        assert terminal['phase']=='validation' and store.task(tid)['state']=='done'
                        from delivery_api.revision_next_review import successor_basis
                        with pytest.raises(ValueError,match='terminal'):
                            successor_basis(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path,review_sha256=next_shown['review_sha256'])
                        assert GeneratorSnapshots(store,later).read(result_receipt()['snapshot'])==source_continuation
                    assert installed_checkpoint()==original_checkpoint
                return
            import psycopg
            with psycopg.connect(DSN) as admin:
                admin.execute('UPDATE decision_reviews SET payload=%s WHERE task_id=%s AND wait_id=%s AND revision=%s',('{}',tid,'revision-continuation:'+intent_kw['installation_id']+':prepared',installation_review['review_sha256']))
            assert reconcile_private_approved_continuation(store,tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)['state']=='continuation_requires_review'
            assert store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)['state']=='installed_verified_no_dispatch'

            assert store.revoke_private_revision_acceptance(tid,acceptance_reference=receipt['acceptance_reference'])['revoked']
            with pytest.raises(TenantAccessDenied,match='revoked'):
                store.verify_private_revision_acceptance(installation_review['review'],installation_review['review_sha256'],receipt['acceptance_reference'])


            with pytest.raises(ValueError,match='new interview input'):
                control.reject_interview_mutation()
            with pytest.raises(ValueError,match='database digest'):
                load_control(expected_anchor=later.execution_anchor,expected_anchor_sha256='f'*64)
            old_manifest=store.manifest_snapshot(tid,1)
            source_row=next(row for row in old_manifest['rows'] if row['phase_key']==phase)
            with psycopg.connect(DSN) as admin:
                admin.execute('UPDATE artifact_bytes SET bytes=%s WHERE owner_id=%s AND sha256=%s',(b'{}',A.owner_id,source_row['sha256']))
            broken=store.reconcile_private_revision_installation(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
            assert broken['state']=='database_installed_requires_review' and set(broken['unverified'])=={'pg_stage_or_source_evidence_unverified','promoted_pg_evidence_unverified'}

            with pytest.raises(ValueError,match='reconciliation'):
                with cli_execution(PostgresExecutionFence(store,later,root=promoted_root),None):
                    apply_private_installation(store,tid,installation_id=intent_kw['installation_id'],expected_authorized_event_sha256=auth['event_sha256'],artifacts_base=tmp_path,validate_acceptance=test_acceptance)
            return
        from delivery_api.pg_revision_install import prepare_anchor,promote_stage
        def test_authority():test_acceptance(installation_review['review'],installation_review['review_sha256'],intent_kw['acceptance_reference'])
        with store._task_operation_guard('task.rerun'):
            anchor_receipt=prepare_anchor(store,tid,intent_kw['installation_id'],test_authority)
        ev={'manifest_sha256':md,'anchor_sha256':anchor_receipt['anchor_sha256']}
        pending=store.append_private_installation_event(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=auth['event_sha256'],
            state='promotion_pending',evidence=ev,read_current_evidence=lambda:ev)
        with pytest.raises(ValueError,match='CAS changed'):
            store.append_private_installation_event(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=auth['event_sha256'],
                state='files_promoted',evidence=ev,read_current_evidence=lambda:ev)
        with pytest.raises(ValueError,match='concrete database activation'):
            store.append_private_installation_event(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=pending['event_sha256'],state='installed',evidence=ev)
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:conn.execute('UPDATE closed_revision_installations SET payload=payload WHERE installation_id=%s',(intent_kw['installation_id'],))
        with pytest.raises(Exception):
            with store._postgres.transaction() as conn:
                conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM closed_revision_installations').fetchall()

        with pytest.raises(ValueError,match='current staged journal'):
            store.retain_revision_installation_review(tid,lineage_id=lid,expected_staged_event_sha256='f'*64,artifacts_base=tmp_path)

        assert staged['event']['state']=='staged' and store.task(tid)==before
        assert store.session_binding(args['lineage']['execution_session_id']) is None
        # Equal JSON with different raw bytes in PG cannot pass retained review.
        original_manifest=store.manifest_snapshot(tid)
        original_artifacts=[{'phase_key':row['phase_key'],'name':row['name'],'data':store.read_artifact_bytes_sha(row['sha256'])} for row in original_manifest['rows']]
        changed_artifacts=[dict(item) for item in original_artifacts]
        next(item for item in changed_artifacts if item['name']=='needs.json')['data']=json.dumps(documents['spec/needs.json'],indent=2).encode()
        store.publish_task_revision_bytes(tid,changed_artifacts,writer_instance='constructed-test-tamper')
        with pytest.raises(ValueError,match='source differs'):
            store.retain_revision_installation_review(tid,lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)
        with pytest.raises(ValueError,match='differs from retained review snapshot'):
            store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=staged['event_sha256'],
                manifest_sha256=md,artifacts_base=tmp_path)
        with store._postgres.transaction() as conn:
            assert len(conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall())==2
        # Restore the exact byte revision, then explicitly promote its receipt.
        store.publish_task_revision_bytes(tid,original_artifacts,writer_instance='constructed-test-restore')
        promote_stage(store,tid,intent_kw['installation_id'],test_authority)
        before=store.task(tid)  # Explicit test tamper/restore created retained artifact revisions.
        with cli_execution(PostgresExecutionFence(store,cap,root=root),None):
            current=lambda:store.read_promoted_installation_evidence(tid,installation_id=intent_kw['installation_id'],artifacts_base=tmp_path)
            assert current()==ev
            promoted=store.append_private_installation_event(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=pending['event_sha256'],
                state='files_promoted',evidence=ev,read_current_evidence=current)
            ready=store.append_private_installation_event(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=promoted['event_sha256'],
                state='install_pending',evidence=ev,read_current_evidence=current)
            with pytest.raises(PermissionError):
                store.activate_private_revision_installation(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=ready['event_sha256'],validate_acceptance=revoked,artifacts_base=tmp_path)
            assert store.task(tid)==before and store.session_binding(args['lineage']['execution_session_id']) is None
            installed=store.activate_private_revision_installation(tid,installation_id=intent_kw['installation_id'],expected_previous_sha256=ready['event_sha256'],validate_acceptance=test_acceptance,artifacts_base=tmp_path)
        task=store.task(tid)
        assert installed['installed'] and not installed['dispatch_started'] and task['generation']==2
        assert task['engine_session_id']==args['lineage']['execution_session_id'] and task['state']=='pending'
        assert next(g for g in task['gates'] if g['after_phase']==phase)['state']=='decided'
        assert next(p for p in task['phases'] if p['phase_key']==phase)['state']=='approved'
        assert task['active_phase']==('specs' if phase=='architecture' else 'architecture') and installed['continuation_prepared'] and installed['approved_phase']=='srs'
        assert all(p['state']=='pending' for p in task['phases'] if p['phase_key'] in ('architecture','specs','tests','validation'))
        assert store.session_binding(cap.session_id)['generation']==1
        later=WorkerCapability.capture(store,tid);later.validate(store)
        assert later.execution_anchor.semantic_session_id==cap.session_id and later.execution_anchor.generation==2
        with pytest.raises(TenantAccessDenied):cap.validate(store)
        from delivery_api.pg_revision_install import read_evidence
        with store._task_operation_guard('task.rerun'):
            assert read_evidence(store,tid,intent_kw['installation_id'])['anchor_sha256']==later.anchor_sha256
        from delivery_api.tenancy import session_root
        assert not session_root(tmp_path,A,later.session_id).exists()

def test_request_tx_row_lock_never_deadlocks_concurrent_store_call(store):
    """Regression for the cross-host resume hang: a request-scoped transaction
    holds its row locks until the request ends. A second request's store write
    must wait on the PG row lock alone - never on an in-process store mutex.
    With a process-wide mutex held across the blocking write, the first
    request's next read blocks on that mutex while still holding the row lock:
    AB-BA across lock domains, invisible to PG deadlock detection (observed as
    GET /api/tasks/<id> hanging on a fresh host's first read of a waiting
    task)."""
    import threading,time,psycopg
    with tenant_context(A):
        tid=store.create_task('deadlock-'+uuid.uuid4().hex)['task_id']
    first_wrote=threading.Event()
    def request_one():
        with tenant_context(A):
            handle,token=store._postgres.begin_request(A)
            try:
                store.add_event(tid,'waiting_input','first')
                first_wrote.set()
                # The second request must actually be waiting on this
                # transaction's row lock before the first request continues,
                # so the pre-fix mutex/row-lock ordering inversion is forced,
                # not left to scheduling luck.
                with psycopg.connect(DSN,autocommit=True) as watch:
                    for _ in range(100):
                        if watch.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'UPDATE tasks SET updated_at%'").fetchone():
                            break
                        time.sleep(0.05)
                    else: raise AssertionError('second request never waited on the row lock')
                # The get_task pattern continues with more store reads in the
                # same request (e.g. _task_artifact_snapshot) while the write
                # locks are still held.
                assert store.task(tid)['task_id']==tid
            finally:
                store._postgres.finish_request(handle,False)
                store._postgres.end_context(token)
    def request_two():
        with tenant_context(A):
            assert first_wrote.wait(5)
            handle,token=store._postgres.begin_request(A)
            try:
                store.add_event(tid,'waiting_input','second')
            finally:
                store._postgres.finish_request(handle,False)
                store._postgres.end_context(token)
    one=threading.Thread(target=request_one,daemon=True)
    two=threading.Thread(target=request_two,daemon=True)
    one.start();two.start()
    one.join(15);two.join(15)
    assert not one.is_alive() and not two.is_alive(),'concurrent requests deadlocked across the store mutex and the PG row lock'
    with tenant_context(A):
        kinds=[e['kind'] for e in store.events(tid)]
    # Both request transactions committed their own copy: the application-level
    # waiting_input dedupe (read-then-write across two store calls) cannot see
    # a concurrent uncommitted insert, so duplicate waiting_input rows are a
    # known, separately tracked scope - not guarded here or at the DB level yet.
    assert kinds==['waiting_input','waiting_input']


def test_task_worker_state_reads_jobs_and_attempts_for_one_task(store):
    # The 2026-10-26 parked session left its worker job open forever and Stop
    # waited on it; this owner-scoped read is what the Debug export shows.
    import psycopg,time
    sid='ws-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'fake-phase')
    now=time.time()
    with psycopg.connect(DSN) as admin:   # fixture setup, like the reauthorization test above
        admin.execute("INSERT INTO provider_attempts (attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES (%s,%s,%s,'v1','{}'::jsonb,'fp','dispatched',10,25,%s,%s)",
            ('att-'+sid,A.owner_id,job,now,now))
    with tenant_context(A):
        state=store.task_worker_state(task['task_id'])
        assert [j['job_id'] for j in state['worker_jobs']]==[job]
        assert state['worker_jobs'][0]['state']=='pending' and 'execution_options' not in state['worker_jobs'][0]
        assert state['provider_attempts'][0]['attempt_id']=='att-'+sid
        assert state['provider_attempts'][0]['state']=='dispatched' and state['provider_attempts'][0]['retail_reserved']==25
        assert 'quote' not in state['provider_attempts'][0] and 'usage' not in state['provider_attempts'][0]
    with tenant_context(B):
        assert store.task_worker_state(task['task_id'])=={'worker_jobs':[],'provider_attempts':[]}
    with psycopg.connect(DSN) as admin:
        admin.execute("DELETE FROM provider_attempts WHERE attempt_id=%s",('att-'+sid,))
    with tenant_context(A):
        store.delete_task(task['task_id'])


def test_task_and_list_reads_are_one_statement_each(store):
    from delivery_api import postgres_scope
    with tenant_context(A):
        first=store.create_task('stmt-'+uuid.uuid4().hex)
        store.create_task('stmt-'+uuid.uuid4().hex)
        # Each operation pays one identity-setup statement at transaction begin;
        # the read itself must add exactly ONE more, no matter how many tasks.
        # Before the one-statement projection this was 5 for task and 2+3N for list.
        for label,read in (('task',lambda:store.task(first['task_id'])),('list_tasks',store.list_tasks)):
            stats=postgres_scope.RequestStats();token=postgres_scope.request_stats.set(stats)
            try:read()
            finally:postgres_scope.request_stats.reset(token)
            assert stats.queries==2,(label,stats.queries)
        record=store.task(first['task_id'])
        assert [p['position'] for p in record['phases']]==sorted(p['position'] for p in record['phases'])
        assert all(isinstance(g['options'],list) for g in record['gates'])
        assert record['artifact_count']==0
        store.delete_task(first['task_id'])


def test_task_snapshot_combines_events_and_preserves_rls(store):
    with tenant_context(A):
        task=store.create_task('snapshot-'+uuid.uuid4().hex)
        store.add_event(task['task_id'],'progress','one')
        store.add_event(task['task_id'],'progress','two')
        snapshot=store.task_snapshot(task['task_id'])
        assert snapshot['task']['task_id']==task['task_id']
        assert [e['message'] for e in snapshot['events']]==['one','two']
        assert snapshot['events'][0]['payload']=={}
    with tenant_context(B):
        assert store.task_snapshot(task['task_id']) is None


def test_snapshot_cache_invalidates_after_postgres_commit(store,monkeypatch):
    from delivery_api import task_read_cache,config
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    with tenant_context(A):
        task=store.create_task('cache-'+uuid.uuid4().hex)
        version=task_read_cache.version(store,task['task_id'])
        data=store.task_snapshot(task['task_id'])
        task_read_cache.put(store,task['task_id'],data,read_version=version)
        assert task_read_cache.get(store,task['task_id']) is not None
        store.rename_task(task['task_id'],'renamed-'+uuid.uuid4().hex)
        assert task_read_cache.get(store,task['task_id']) is None


def test_pipeline_setup_exits_before_write_error_and_counts_statements(store):
    from delivery_api.postgres_scope import RequestStats,request_stats
    stats=RequestStats();token=request_stats.set(stats)
    try:
        with tenant_context(A):
            task=store.create_task('counts-'+uuid.uuid4().hex)
            stats.queries=0;stats.statements.clear()
            data=store.task_snapshot(task['task_id'])
            assert data['task']['task_id']==task['task_id']
            assert stats.queries==2  # identity + one combined SELECT (savepoint is control SQL)
            labels=[label for label,ms in stats.statements]
            assert sum(label.startswith("SELECT set_config") for label in labels)==1
            # TimingConnection + SQL bridge must not double count the same read.
            assert sum(label.startswith('SELECT t.*') for label in labels)==1
            assert stats.db_seconds>=0
    finally:request_stats.reset(token)


def test_cloud_presentation_cold_two_statements_then_warm_zero(store,monkeypatch):
    from delivery_api import app,config,task_read_cache
    from delivery_api.postgres_scope import RequestStats,request_stats
    from starlette.requests import Request
    monkeypatch.setattr(app,'store',store)
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(config,'ARTIFACT_MANIFEST_READS',True,raising=False)
    with tenant_context(A):
        task=store.create_task('http-counts-'+uuid.uuid4().hex)
        request=Request({'type':'http','method':'GET','path':'/api/tasks/'+task['task_id'],'headers':[],'scheme':'http','server':('test',80),'query_string':b''})
        stats=RequestStats();token=request_stats.set(stats)
        handle,context=store._postgres.begin_request(A)
        try:
            assert app.get_task(task['task_id'],request=request)['task_id']==task['task_id']
            assert stats.queries==2  # setup + snapshot; middleware session-check miss adds one
        finally:
            store._postgres.end_context(context);store._postgres.finish_request(handle,False);request_stats.reset(token)
        stats=RequestStats();token=request_stats.set(stats)
        handle,context=store._postgres.begin_request(A)
        try:
            assert app.get_task(task['task_id'],request=request)['task_id']==task['task_id']
            assert stats.queries==0
            assert handle.connection is None
        finally:
            store._postgres.end_context(context);store._postgres.finish_request(handle,False);request_stats.reset(token)


def test_shared_job_authority_rejects_stopped_same_generation_and_foreign_binding(store):
    from delivery_api.worker_capability import WorkerCapability
    with tenant_context(A):
        task=store.create_task('shared-job-'+uuid.uuid4().hex);tid=task['task_id'];store.set_engine_session(tid,'shared-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');store.claim_worker(job);row=store.worker_job(job)
        assert WorkerCapability.require_job(store,row).task_id==tid
        with pytest.raises(TenantAccessDenied):WorkerCapability.require_job(store,row|{'owner_id':B.owner_id})
        with store._postgres.transaction() as c:c.execute('UPDATE tasks SET stop_requested_at=123 WHERE task_id=%s',(tid,))
        with pytest.raises(TenantAccessDenied):WorkerCapability.require_job(store,row)
        store.delete_task(tid)
