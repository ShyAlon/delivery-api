"""Stop proof must not pay one retained-receipt query per child job."""
import uuid
import pytest
import psycopg
from test_postgres_store import store,DSN,A
from delivery_api.tenancy import tenant_context


@pytest.mark.parametrize('job_count',[1,5])
def test_reaped_child_stop_proof_reads_all_receipts_in_one_statement(store,monkeypatch,job_count):
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.db_pool import pool_for
    with tenant_context(A):
        tid=store.create_task('stop proof '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4())
        store.set_engine_session(tid,sid)
        jobs=[]
        for index in range(job_count):
            job=store.enqueue_worker(tid,'phase:intent:'+str(index));store.claim_worker(job);jobs.append(job)
            store.retain_decision_review(tid,'child-job:'+job,'child-completion-v1',{'reaped':True})
        store.request_task_stop(tid)
        pg=PgSessionStore(pool_for(store._postgres.dsn),claims=(A.owner_id,A.session_id));pg.stop(sid,A.owner_id)
        for job in jobs:store.finish_stopped_worker(job,worker_quiescent=True)
        calls=[];original=store._execute_native
        def counted(sql,*args,**kwargs):
            calls.append(sql)
            return original(sql,*args,**kwargs)
        monkeypatch.setattr(store,'_execute_native',counted)
        assert store.confirm_child_session_quiescent(sid) is True
        receipt_reads=[sql for sql in calls if 'decision_reviews' in sql]
        assert len(receipt_reads)==1, 'child receipts must be set-based, not one read per job'
        # A receipt for some other child is not proof for a missing one.
        with psycopg.connect(DSN) as admin:
            admin.execute("DELETE FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision='child-completion-v1'",(tid,'child-job:'+jobs[-1]))
        assert store.confirm_child_session_quiescent(sid) is False


@pytest.mark.parametrize('proof',[None,{}, {'reaped':False},{'reaped':'true'},{'reaped':1}])
def test_child_stop_requires_exact_reaped_boolean_for_each_job(store,proof):
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.db_pool import pool_for
    with tenant_context(A):
        tid=store.create_task('strict stop receipt '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4())
        store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:intent');store.claim_worker(job)
        if proof is not None:store.retain_decision_review(tid,'child-job:'+job,'child-completion-v1',proof)
        store.request_task_stop(tid)
        pg=PgSessionStore(pool_for(store._postgres.dsn),claims=(A.owner_id,A.session_id));pg.stop(sid,A.owner_id)
        store.finish_stopped_worker(job,worker_quiescent=True)
        assert store.confirm_child_session_quiescent(sid) is False
