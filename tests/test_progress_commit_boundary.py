"""Deterministic post-commit visibility, rollback silence, and request batching."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from delivery_api import event_bus
from delivery_api.tenancy import tenant_context


def test_notify_sees_committed_row_on_independent_connection(pg_scoped_workspace,monkeypatch):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('committed visibility')['task_id'];seen=[]
    def read():
        with tenant_context(w.scope):return any(e['message']=='probe' for e in s.events(t))
    def notify(task):
        assert task==t
        with ThreadPoolExecutor(1) as executor:seen.append(executor.submit(read).result(timeout=10))
    monkeypatch.setattr(event_bus,'notify',notify)
    s.add_event(t,'progress','probe')
    assert seen==[True]


def test_request_batch_deduplicates_and_publishes_only_on_commit(pg_scoped_workspace,monkeypatch):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('shared commit')['task_id'];seen=[]
    monkeypatch.setattr(event_bus,'notify',seen.append)
    handle,token=s._postgres.begin_request(w.scope)
    try:
        s.add_event(t,'progress','one');s.add_event(t,'progress','two')
        assert seen==[]
    finally:s._postgres.end_context(token)
    s._postgres.finish_request(handle,False)
    assert seen==[t]


def test_request_rollback_never_publishes(pg_scoped_workspace,monkeypatch):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('rollback silence')['task_id'];seen=[]
    monkeypatch.setattr(event_bus,'notify',seen.append)
    handle,token=s._postgres.begin_request(w.scope)
    try:s.add_event(t,'progress','rolled back')
    finally:s._postgres.end_context(token)
    s._postgres.finish_request(handle,True)
    assert seen==[] and not s.events(t)


def test_failed_operation_does_not_leave_a_request_wake(pg_scoped_workspace,monkeypatch):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('failed operation')['task_id'];seen=[]
    original=s._execute_native
    def fail(sql,*args,**kw):
        result=original(sql,*args,**kw)
        if sql.startswith('WITH ev AS (INSERT INTO events'):raise RuntimeError('fixture rollback')   # add_event is one statement (event + updated_at touch); fail after it wrote
        return result
    monkeypatch.setattr(event_bus,'notify',seen.append)
    handle,token=s._postgres.begin_request(w.scope)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(s,'_execute_native',fail)
            with pytest.raises(RuntimeError):s.add_event(t,'progress','not committed')
    finally:s._postgres.end_context(token)
    s._postgres.finish_request(handle,False)
    assert seen==[] and not s.events(t)


def test_standalone_transaction_failure_discards_queued_wake(pg_scoped_workspace,monkeypatch):
    s=pg_scoped_workspace.store;seen=[];monkeypatch.setattr(event_bus,'notify',seen.append)
    with pytest.raises(RuntimeError):
        with s._postgres.transaction() as connection:
            connection.notify_after_commit('never-committed')
            raise RuntimeError('rollback')
    assert seen==[]


def test_failed_nested_store_operation_discards_only_its_wake(pg_scoped_workspace,monkeypatch):
    from delivery_api.postgres_store import _transactional
    w=pg_scoped_workspace;s=w.store;seen=[];monkeypatch.setattr(event_bus,'notify',seen.append)
    @_transactional
    def operation(store):
        store._connection.get().notify_after_commit('failed-operation')
        raise RuntimeError('rollback nested method')
    handle,token=s._postgres.begin_request(w.scope)
    try:
        with s._postgres.transaction() as connection:connection.notify_after_commit('successful-operation')
        with pytest.raises(RuntimeError):operation(s)
        assert seen==[]
    finally:s._postgres.end_context(token)
    s._postgres.finish_request(handle,False)
    assert seen==['successful-operation']
