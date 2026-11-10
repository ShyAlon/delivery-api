"""Batch only independent reads after the owner lock, never the mutating totals function."""
import uuid
from test_attempt_ledger import ledger, Q
from test_postgres_store import A
from delivery_api.tenancy import tenant_context
from delivery_api import postgres_scope


def test_reservation_reads_use_one_sync_without_relaxing_caps(ledger):
    store, tid, job = ledger
    stats=postgres_scope.RequestStats()
    with tenant_context(A):
        token=postgres_scope.request_stats.set(stats)
        try:store.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'batch-test')
        finally:postgres_scope.request_stats.reset(token)
    reads=[e for e in stats.trace if any(x in e[1] for x in ('SELECT suspended FROM user_task_policy','SELECT * FROM admission_limits','SELECT * FROM provider_attempts WHERE attempt_id'))]
    # First three reads form one batch. Main has a final post-INSERT read; V1 uses RETURNING instead.
    assert len(reads) in (3,4)
    assert sum(e[3] for e in reads[:3])==1,reads
    assert sum(e[3] for e in reads)<=2,reads
    assert sum(e[3] for e in stats.trace)<=12  # main:14; no authority read or cap fence removed


def test_batch_keeps_totals_conditional_on_replay(ledger,monkeypatch):
    import pytest
    from delivery_api.attempt_ledger import AdmissionDenied
    s,tid,job=ledger;aid=uuid.uuid4().hex
    with tenant_context(A):
        first=s.reserve_provider_attempt(job,aid,Q,'conditional-test')
        original=s._execute_native
        def no_totals(sql,*args,**kwargs):
            if 'requi_admission_totals' in sql:raise AssertionError('mutating totals must not run on replay/refusal')
            return original(sql,*args,**kwargs)
        monkeypatch.setattr(s,'_execute_native',no_totals)
        assert s.reserve_provider_attempt(job,aid,Q,'conditional-test')==first
        with pytest.raises(AdmissionDenied,match='conflicting'):
            s.reserve_provider_attempt(job,aid,Q,'different')


def test_pipeline_failure_raises_and_outer_transaction_rolls_back(ledger):
    import pytest,psycopg
    s,tid,job=ledger
    with tenant_context(A):
        with pytest.raises(psycopg.errors.DivisionByZero):
            with s._postgres.transaction() as conn:
                conn.read_batch((('SELECT 1 AS valid',()),('SELECT 1/0 AS invalid',())))
        # A failed batch does not poison the next transaction or return a partial successful result.
        assert s.worker_job(job)['job_id']==job
