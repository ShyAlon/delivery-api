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


def _restart_intent_fixture(store):
    from delivery_api.gate_revision import save_closed_revision,closed_revision_preflight
    from test_closed_revision_job import options
    from test_revision_intent import proposal
    tid=store.create_task('revision-intent-'+uuid.uuid4().hex)['task_id'];sid='rev-'+uuid.uuid4().hex
    store.set_engine_session(tid,sid);store.wait_at_gate(tid,'srs')
    store.retain_decision_review(tid,'gate:gate-srs','review',{'task':store.task(tid)})
    store.decide_gate(tid,'gate-srs','approve',selected_option='Proceed')
    draft=save_closed_revision(store,tid,'gate-srs','review','Describe offline behavior')['draft_id']
    o=options();o['draft_id']=draft;o['gate_revision']=next(g for g in store.task(tid)['gates'] if g['gate_key']=='gate-srs')['revision']
    live=closed_revision_preflight(store,tid,'gate-srs','review',draft,artifact_snapshot=o['artifact_evidence'])
    o['preflight_digest']=live['review_digest'];p=proposal()|{'task_id':tid,'session_id':sid}
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


def test_real_sqlite_files_and_pg_retained_snapshot_to_staged_journal(store,tmp_path,monkeypatch):
    source_path=os.getenv("DELIVERY_TEST_SPEC_SOURCE")
    if source_path:monkeypatch.syspath_prepend(source_path)
    import hashlib,json
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.gate_revision import closed_revision_preflight
    from test_revision_stage_real import build_real_stage_fixture
    with tenant_context(A):
        tid,o,p,_=_restart_intent_fixture(store);before=store.task(tid)
        cap=WorkerCapability.capture(store,tid);saved={}
        def lineage_factory(options,source,result,digest):
            live=closed_revision_preflight(store,tid,'gate-srs','review',options['draft_id'],artifact_snapshot=options['artifact_evidence'])
            options['preflight_digest']=live['review_digest']
            proposal=p|{'proposal_id':'a'*32,'source_checkpoint_id':options['source_checkpoint_id'],
                'artifact':result['srs'],'artifact_sha256':digest}
            evidence={'artifacts':options['artifact_evidence'],'checkpoint_id':options['source_checkpoint_id'],'proposal_sha256':digest}
            held=store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=options,proposal=proposal,
                approval_reference='constructed-test-review-not-founder-approval',read_current_evidence=lambda:evidence)
            planned=store.retain_closed_revision_lineage_plan(tid,intent_id=held['intent_id'],semantic_session_id='s',
                new_execution_session_id='next-'+uuid.uuid4().hex,retained_proposal={'proposal':proposal,'document_sha256':hashlib.sha256(json.dumps(proposal,sort_keys=True,indent=2).encode()).hexdigest()})
            saved.update(intent_id=held['intent_id'],lineage_id=planned['lineage_id'],proposal=proposal)
            with store._postgres.transaction() as conn:
                return json.loads(conn.execute('SELECT payload FROM closed_revision_lineage_plans WHERE lineage_id=%s',(planned['lineage_id'],)).fetchone()['payload'])
        built=build_real_stage_fixture(tmp_path,cap=cap,options_override=o,lineage_factory=lineage_factory)
        args,root,dest,documents,files,manifest,md,*_=built
        lid=saved['lineage_id']
        review=store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert review['accepted'] is False
        first=store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,state='prepared',
            evidence={'intent_id':saved['intent_id'],'lineage_id':lid})
        import shutil
        from spec_generator.stop_fence import StopFence
        from spec_generator.cooperative_run import cli_execution
        shutil.rmtree(dest)  # Only test-built data; runtime never deletes/retries partial stage.
        (dest.parent.parent/'proposal.json').write_bytes(json.dumps(saved['proposal'],sort_keys=True,indent=2).encode())
        with cli_execution(StopFence(root/'interview'/'stop-request.json'),None):
            staged=store.prepare_revision_staging(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],artifacts_base=tmp_path)
        md=staged['event']['evidence']['manifest_sha256']
        safety=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert safety['blockers']==['source_writer_exclusion_unverified'] and not safety['installation_available']
        with cli_execution(StopFence(root/'interview'/'stop-request.json'),None):
            safety=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert safety['observed_work_quiescent'] and not safety['installation_available']
        pending=store.enqueue_worker(tid,'phase:srs')
        with cli_execution(StopFence(root/'interview'/'stop-request.json'),None):
            blocked=store.inspect_revision_install_safety(tid,lineage_id=lid,artifacts_base=tmp_path)
        assert blocked['unresolved_job_ids']==[pending] and 'worker_jobs_unresolved' in blocked['blockers']
        with store._postgres.transaction() as conn:conn.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s",(pending,))

        installation_review=store.retain_revision_installation_review(tid,lineage_id=lid,
            expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)
        assert not installation_review['review']['accepted'] and not installation_review['review']['installation_available']
        assert store.decision_review(tid,'revision-install:'+lid,installation_review['review_sha256'])==installation_review['review']
        assert store.retain_revision_installation_review(tid,lineage_id=lid,
            expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)==installation_review
        auth_checks=[]
        def test_acceptance(review,sha,reference):
            assert review==installation_review['review'] and sha==installation_review['review_sha256']
            assert reference=='constructed-private-test-acceptance';auth_checks.append(1)
        intent_kw=dict(lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],review_sha256=installation_review['review_sha256'],
            installation_id='install-'+uuid.uuid4().hex,acceptance_reference='constructed-private-test-acceptance',validate_acceptance=test_acceptance,artifacts_base=tmp_path)
        install=store.retain_private_installation_intent(tid,**intent_kw)
        assert not install['replayed'] and not install['plan']['installed'] and len(auth_checks)==2
        assert store.retain_private_installation_intent(tid,**intent_kw)['replayed']
        def revoked(*a):raise PermissionError('test acceptance revoked')
        with pytest.raises(PermissionError):store.retain_private_installation_intent(tid,**(intent_kw|{'validate_acceptance':revoked}))
        with store._postgres.transaction() as conn:
            auth=conn.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=%s',(intent_kw['installation_id'],)).fetchone()
        from delivery_api.revision_install_files import prepare_execution_anchor,promote_private_stage
        def test_authority():test_acceptance(installation_review['review'],installation_review['review_sha256'],intent_kw['acceptance_reference'])
        anchor_receipt=prepare_execution_anchor(stage_root=dest,plan=install['plan'],plan_sha256=install['plan_sha256'],validate_authority=test_authority)
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
        # Equal JSON with different raw bytes cannot pass retained review.
        (root/'spec'/'needs.json').write_text(json.dumps(documents['spec/needs.json'],indent=2))
        with pytest.raises(ValueError,match='source differs'):
            store.retain_revision_installation_review(tid,lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],artifacts_base=tmp_path)
        with pytest.raises(ValueError,match='differs from retained review snapshot'):
            store.record_validated_revision_staging(tid,lineage_id=lid,expected_previous_sha256=staged['event_sha256'],
                manifest_sha256=md,artifacts_base=tmp_path)
        with store._postgres.transaction() as conn:
            assert len(conn.execute('SELECT 1 FROM closed_revision_promotion_events WHERE lineage_id=%s',(lid,)).fetchall())==2
        # Restore exact original bytes, then hermetically exercise private effects.
        (root/'spec'/'needs.json').write_bytes((dest/'spec'/'needs.json').read_bytes())
        promote_private_stage(artifacts_base=tmp_path,lineage=args['lineage'],plan=install['plan'],plan_sha256=install['plan_sha256'],validate_authority=test_authority)
        with cli_execution(StopFence(root/'interview'/'stop-request.json'),None):
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
        assert task['engine_session_id']==args['lineage']['execution_session_id'] and task['state']=='waiting_gate'
        assert next(g for g in task['gates'] if g['after_phase']=='srs')['state']=='waiting'
        assert store.session_binding(cap.session_id)['generation']==1
