"""Rank 14a: stale HTTP snapshots must not duplicate durable question evidence."""
import threading,uuid
from concurrent.futures import ThreadPoolExecutor
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B
from vmodel_contracts.interactive import InputRequest,InputKind


def sync_pair(stores,tid,pending,monkeypatch,scope=None):
    from delivery_api import app as module
    # Both requests already read an empty event snapshot before either writes.
    barrier=threading.Barrier(2)
    local=threading.local()
    class Router:
        def __getattr__(self,name):return getattr(local.store,name)
    monkeypatch.setattr(module,'store',Router())
    def run(store):
        from contextlib import nullcontext
        with tenant_context(scope) if scope else nullcontext():
            local.store=store;events=[]
            if scope:handle,token=store._postgres.begin_request(scope)
            try:
                barrier.wait(timeout=5)
                module._sync_waiting_input_event(tid,pending,events=events,task={'active_phase':'intent'})
                return events
            finally:
                if scope:
                    store._postgres.finish_request(handle,False)
                    store._postgres.end_context(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run,stores))


def test_pg_workspace_two_adapters_stale_snapshot_has_one_durable_event(pg_scoped_workspace,monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    one=pg_scoped_workspace.store;two=PostgresStore(one._postgres.dsn)
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(one,PostgresStore)
    tid=one.create_task('dedupe')['task_id'];pending=InputRequest(input_id=uuid.uuid4(),kind=InputKind.TEXT,prompt='Question?')
    replies=sync_pair([one,two],tid,pending,monkeypatch,pg_scoped_workspace.scope)
    rows=one.events(tid)
    assert len(rows)==1
    assert all(len(reply)==1 and reply[0]['id']==rows[0]['id'] for reply in replies)


def test_postgres_two_request_transactions_stale_snapshot_has_one_event(store,monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    from test_postgres_store import DSN
    with tenant_context(A):tid=store.create_task('dedupe-'+uuid.uuid4().hex)['task_id']
    other=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
    pending=InputRequest(input_id=uuid.uuid4(),kind=InputKind.TEXT,prompt='Question?')
    sync_pair([store,other],tid,pending,monkeypatch,A)
    with tenant_context(A):
        rows=store.events(tid)
        assert len(rows)==1
        assert rows[0]['payload']['input_id']==str(pending.input_id)


def test_postgres_duplicate_keeps_original_and_foreign_owner_cannot_write(store):
    import pytest
    from delivery_api import event_bus
    input_id=str(uuid.uuid4());payload={'input_id':input_id,'waiting_input':True,'question':'Original?'}
    with tenant_context(A):
        tid=store.create_task('identity-'+uuid.uuid4().hex)['task_id']
        first=store.ensure_waiting_input_event(tid,'Original?',phase_key='intent',payload=payload)
        row=store.events(tid)[0];updated=store.task(tid)['updated_at'];wake=event_bus.version(tid)
        again=store.ensure_waiting_input_event(tid,'Changed?',phase_key='conops',payload={**payload,'question':'Changed?'})
        assert again==first and store.events(tid)==[row]
        assert store.task(tid)['updated_at']==updated and event_bus.version(tid)==wake
        other=store.ensure_waiting_input_event(tid,'Next?',phase_key='intent',payload={**payload,'input_id':str(uuid.uuid4())})
        assert other!=first and len(store.events(tid))==2
    with tenant_context(B):
        with pytest.raises(KeyError):store.ensure_waiting_input_event(tid,'Foreign',phase_key='intent',payload=payload)
        assert store.events(tid)==[]
    with tenant_context(A):assert len(store.events(tid))==2


def test_pg_workspace_duplicate_keeps_original_and_invalid_write_rolls_back(pg_scoped_workspace):
    import pytest
    store=pg_scoped_workspace.store;tid=store.create_task('identity')['task_id']
    payload={'input_id':str(uuid.uuid4()),'waiting_input':True}
    first=store.ensure_waiting_input_event(tid,'Original',phase_key='intent',payload=payload)
    row=store.events(tid)[0]
    assert store.ensure_waiting_input_event(tid,'Changed',phase_key='conops',payload=payload)==first
    assert store.events(tid)==[row]
    with pytest.raises(ValueError):store.ensure_waiting_input_event(tid,'invalid',phase_key='intent',payload={})
    assert store._connection.get() is None and store.events(tid)==[row]
    # A subsequent valid write proves the failed transaction did not poison the pool.
    next_id=store.ensure_waiting_input_event(tid,'Next',phase_key='intent',payload={'input_id':str(uuid.uuid4())})
    assert next_id!=first and len(store.events(tid))==2


def test_input_identity_is_checked_beyond_first_event_page(pg_scoped_workspace):
    store=pg_scoped_workspace.store;tid=store.create_task('history')['task_id']
    for i in range(501):store.add_event(tid,'progress',str(i))
    payload={'input_id':str(uuid.uuid4()),'waiting_input':True}
    first=store.ensure_waiting_input_event(tid,'Question',phase_key='intent',payload=payload)
    assert store.ensure_waiting_input_event(tid,'Question',phase_key='intent',payload=payload)==first
    with store._postgres.transaction() as connection:
        rows=connection.execute("SELECT id FROM events WHERE task_id=%s AND kind='waiting_input'",(tid,)).fetchall()
    assert len(rows)==1 and rows[0]['id']==first
