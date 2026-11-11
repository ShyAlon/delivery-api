"""Short helper batches share identity/commit, never provider work."""
import uuid
import json
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.answer_trace import WireTrace


def test_answer_batch_one_transaction_policy_still_runs_and_rollback(pg_workspace_factory,monkeypatch):
    from delivery_api import suspension_policy
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('answer batching')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
        policies=[];original=suspension_policy.require_task_operation
        def checked(store,action):policies.append(action);return original(store,action)
        monkeypatch.setattr(suspension_policy,'require_task_operation',checked)
        trace=WireTrace();trace.install()
        try:
            job=w.store.run_answer_batch(lambda:(w.store.enqueue_worker(tid,'phase:intent'),w.store.retain_decision_review(tid,'one','one',{'ok':True})))[0]
            assert policies
            assert sum(r['syncs'] for r in trace.snapshot() if 'postgres_scope:_begin' in r['caller'])==1
            assert w.store._connection.get() is None
            with pytest.raises(RuntimeError,match='rollback'):
                def fail():
                    w.store.retain_decision_review(tid,'rollback','one',{'ok':False})
                    raise RuntimeError('rollback')
                w.store.run_answer_batch(fail)
            assert w.store.decision_review(tid,'rollback','one') is None
            assert w.store.worker_job(job)['state']=='pending'
        finally:trace.restore()


def test_answer_review_one_statement_keeps_conflicting_bytes_and_rolls_back(pg_workspace_factory):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('minimal review')['task_id']
        trace=WireTrace();trace.install()
        try:
            w.store.retain_answer_review(tid,'input','v1',{'answer':'one'})
            calls=[r for r in trace.snapshot() if 'retain_answer_review' in r['caller'] and 'execute' in r['caller']]
            assert sum(r['syncs'] for r in calls)==1,calls
            w.store.retain_answer_review(tid,'input','v1',{'answer':'one'})
            with pytest.raises(ValueError,match='conflicting'):
                w.store.retain_answer_review(tid,'input','v1',{'answer':'other'})
            assert w.store.decision_review(tid,'input','v1')=={'answer':'one'}
        finally:trace.restore()


def test_answer_admission_checks_policy_once_without_duplicate_outer_lock(pg_workspace_factory,monkeypatch):
    from delivery_api import suspension_policy,prepaid
    from delivery_api.postgres_store import answer_admission
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('single answer policy')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
        calls=[];original=suspension_policy.require_task_operation
        def checked(store,action):calls.append(action);return original(store,action)
        monkeypatch.setattr(suspension_policy,'require_task_operation',checked)
        def forbidden(*a):raise AssertionError('duplicate owner lock')
        with monkeypatch.context() as patch,answer_admission(w.store):
            patch.setattr(prepaid,'owner_lock',forbidden)
            job=w.store.run_answer_batch(lambda:w.store.enqueue_worker(tid,'phase:intent'))
            assert w.store.claim_worker(job) is not None
        assert calls==['task.answer']


def test_final_answer_worker_rejects_stop_and_scope_change(pg_workspace_factory):
    from delivery_api.tenancy import TenantScope,TenantAccessDenied
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('final answer fence')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
        job=w.store.enqueue_worker(tid,'phase:intent');cap=w.store.claim_worker(job)
        with tenant_context(TenantScope(str(uuid.uuid4()),str(uuid.uuid4()))),pytest.raises(TenantAccessDenied):
            w.store.finish_answer_worker(cap,job)
        w.store.request_task_stop(tid)
        with pytest.raises(TenantAccessDenied,match='stale, stopped or revoked'):w.store.finish_answer_worker(cap,job)
        assert w.store.worker_job(job)['state']=='running'


def test_final_completion_composite_keeps_conflicting_record_and_rolls_back_event(pg_workspace_factory):
    from delivery_api.tenancy import TenantAccessDenied
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('composite completion')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
        job=w.store.enqueue_worker(tid,'phase:intent');cap=w.store.claim_worker(job)
        w.store.retain_answer_review(tid,'child-job:'+job,'child-completion-v1',{'original':True})
        with w.admin() as c:c.execute('UPDATE engine_sessions SET control=%s::jsonb WHERE session_id=%s',(json.dumps({'sequence':2,'_answer_operation_id':job}),sid))
        before=w.store.events(tid)
        with pytest.raises(TenantAccessDenied,match='conflicting'):
            w.store.finish_answer_worker(cap,job,completion={'different':True,'published_sequence':2},event=('complete',{'job_id':job}))
        assert w.store.worker_job(job)['state']=='running'
        assert w.store.events(tid)==before
        assert w.store.decision_review(tid,'child-job:'+job,'child-completion-v1')=={'original':True}


def test_task_event_batch_order_atomicity_and_scope(pg_workspace_factory,monkeypatch):
    from delivery_api.postgres_scope import RequestStats,request_stats
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        with tenant_context(a.scope):
            tid=a.store.create_task('Atomic event batch')['task_id']
            rows=[{'kind':'answer_submitted','message':'submitted'},{'kind':'progress','message':'working','payload':{'stage':'answer.engine'}}]
            stats=RequestStats();token=request_stats.set(stats)
            try:ids=a.store.add_event_batch(tid,rows)
            finally:request_stats.reset(token)
            assert sum(e[3] for e in stats.trace)==3
            assert ids==sorted(ids) and len(ids)==2
            assert [e['message'] for e in a.store.events(tid)]==['submitted','working']
            with pytest.raises(Exception):a.store.add_event_batch(tid,[rows[0],{'kind':None,'message':'invalid'}])
            assert len(a.store.events(tid))==2
        with tenant_context(b.scope),pytest.raises(Exception):a.store.add_event_batch(tid,rows)
