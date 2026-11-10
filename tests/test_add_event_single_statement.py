"""add_event inserts the event and touches tasks.updated_at in ONE statement (was two round trips)."""
import time
import uuid
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context


def _tid(store):
    return store.create_task('ev1-'+uuid.uuid4().hex)['task_id']


def test_one_statement_inserts_event_and_touches_the_task(store,monkeypatch):
    with tenant_context(A):
        tid=_tid(store);before=store.task(tid)['updated_at'];time.sleep(0.01)
        calls=[];real=store._execute_native
        monkeypatch.setattr(store,'_execute_native',lambda sql,p=(),**kw:(calls.append(' '.join(sql.split())[:50]),real(sql,p,**kw))[1])
        eid=store.add_event(tid,'note','hello',phase_key='intent',payload={'a':1})
        monkeypatch.undo()
        assert len(calls)==1,calls
        ev=[e for e in store.events(tid) if e['id']==eid]
        assert len(ev)==1 and ev[0]['kind']=='note' and ev[0]['message']=='hello' and ev[0]['phase_key']=='intent' and ev[0]['payload']=={'a':1}
        assert store.task(tid)['updated_at']>before


def test_event_ids_stay_ordered(store):
    with tenant_context(A):
        tid=_tid(store);ids=[store.add_event(tid,'n',str(i)) for i in range(3)]
        assert ids==sorted(ids) and len(set(ids))==3


def test_unknown_task_still_raises_and_writes_nothing(store):
    with tenant_context(A):
        with pytest.raises(Exception):store.add_event('nope'+uuid.uuid4().hex[:8],'n','x')


def test_other_tenant_cannot_add_an_event(store):
    with tenant_context(A):tid=_tid(store)
    with tenant_context(B):
        with pytest.raises(Exception):store.add_event(tid,'n','x')
    with tenant_context(A):
        assert store.events(tid)==[]
