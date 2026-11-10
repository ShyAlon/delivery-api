"""task_for_engine_session is remembered per tenant and dropped by every write that can change the mapping."""
import uuid
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context


def _spy(store,monkeypatch):
    calls=[];real=store._execute_native
    monkeypatch.setattr(store,'_execute_native',lambda sql,p=(),**kw:(calls.append(' '.join(sql.split())[:40]),real(sql,p,**kw))[1])
    return calls


def _bound(store):
    tid=store.create_task('stc-'+uuid.uuid4().hex)['task_id'];sid='stc-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
    return tid,sid


def test_second_lookup_costs_no_statement(store,monkeypatch):
    with tenant_context(A):
        tid,sid=_bound(store)
        assert store.task_for_engine_session(sid)==tid
        calls=_spy(store,monkeypatch)
        assert store.task_for_engine_session(sid)==tid
        assert calls==[],calls


def test_other_tenant_never_sees_the_cached_mapping(store):
    with tenant_context(A):
        tid,sid=_bound(store);assert store.task_for_engine_session(sid)==tid
    with tenant_context(B):
        assert store.task_for_engine_session(sid) is None


def test_rebinding_the_session_drops_the_entry(store,monkeypatch):
    with tenant_context(A):
        tid,sid=_bound(store);assert store.task_for_engine_session(sid)==tid
        sid2='stc2-'+uuid.uuid4().hex
        with store._postgres.transaction() as c:c.execute('INSERT INTO engine_sessions SELECT %s,owner_id,task_id,generation,%s FROM engine_sessions WHERE session_id=%s',(sid2,1.0,sid))
        store.set_engine_session(tid,sid2)      # the store's own write: both mapping directions must follow
        assert store.task_for_engine_session(sid) is None and store.task_for_engine_session(sid2)==tid


def test_deleting_the_task_drops_the_entry(store):
    with tenant_context(A):
        tid,sid=_bound(store);assert store.task_for_engine_session(sid)==tid
        store.delete_task(tid)
        assert store.task_for_engine_session(sid) is None


def test_an_unrelated_write_keeps_the_entry(store,monkeypatch):
    with tenant_context(A):
        tid,sid=_bound(store);assert store.task_for_engine_session(sid)==tid
        store.add_event(tid,'note','x')
        calls=_spy(store,monkeypatch)
        assert store.task_for_engine_session(sid)==tid and calls==[]


def test_a_rolled_back_rebind_leaves_no_wrong_entry(store):
    with tenant_context(A):
        tid,sid=_bound(store);assert store.task_for_engine_session(sid)==tid
        try:
            with store._postgres.transaction() as c:
                token=store._connection.set(c)
                try:store._execute_native('UPDATE tasks SET engine_session_id=%s WHERE task_id=%s',('other-'+sid,tid),write=True)
                finally:store._connection.reset(token)
                raise RuntimeError('rollback')
        except RuntimeError:pass
        assert store.task_for_engine_session(sid)==tid
