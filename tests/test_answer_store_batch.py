"""Short helper batches share identity/commit, never provider work."""
import uuid
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
