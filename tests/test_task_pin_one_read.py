"""Immutable task-generation pins still come from PG, without two transactions per lookup."""
import uuid
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context
from delivery_api import postgres_scope,model_pins


def test_existing_generation_pin_is_one_statement_in_one_transaction(store,monkeypatch):
    with tenant_context(A):
        tid=store.create_task('pin-read-'+uuid.uuid4().hex)['task_id']
        expected={'reviewed':'first'}
        store.retain_decision_review(tid,'model-pins:1','model-pins-v1',expected)
        factory=model_pins.DurableGateways(store,{})
        monkeypatch.setattr(factory,'_factory',lambda env:env)
        stats=postgres_scope.RequestStats();token=postgres_scope.request_stats.set(stats)
        try:assert factory.for_task(tid)==expected
        finally:postgres_scope.request_stats.reset(token)
        assert stats.transactions==1,stats.transactions
        assert stats.queries==2,stats.queries  # bookkeeping includes scoped batch plus statement
        assert sum(e[3] for e in stats.trace)==1,stats.trace  # same fresh read, one pipelined Sync
        # No cache: a later generation gets only its own durable pin, never the old one.
        import psycopg
        from test_postgres_store import DSN
        with psycopg.connect(DSN) as conn:conn.execute('UPDATE tasks SET generation=2 WHERE task_id=%s',(tid,))
        store.retain_decision_review(tid,'model-pins:2','model-pins-v1',{'reviewed':'second'})
        assert factory.for_task(tid)=={'reviewed':'second'}
    with tenant_context(B),pytest.raises(KeyError):factory.for_task(tid)
    with tenant_context(A):
        store.delete_task(tid)
        with pytest.raises(KeyError):factory.for_task(tid)


def test_current_generation_without_a_pin_never_returns_previous_generation_pin(store):
    from test_postgres_store import DSN
    import psycopg
    with tenant_context(A):
        tid=store.create_task('pin-empty-'+uuid.uuid4().hex)['task_id']
        store.retain_decision_review(tid,'model-pins:1','model-pins-v1',{'old':'must not leak'})
        with psycopg.connect(DSN) as conn:conn.execute('UPDATE tasks SET generation=2 WHERE task_id=%s',(tid,))
        assert store.task_model_pin(tid)=={'generation':2,'environment':None}
        store.delete_task(tid)
