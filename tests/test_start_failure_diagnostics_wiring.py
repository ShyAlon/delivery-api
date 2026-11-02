"""The session-start failure path records the engine's operator block on the
dedicated diagnostics store while the customer event text stays minimal."""
import asyncio
from types import SimpleNamespace as NS

import delivery_api.app as module
from delivery_api.engine_client import EngineClientError
from vmodel_contracts.interactive import ProtocolError, ProtocolErrorCode

BLOCK = {'error_class': 'InsufficientPrivilege', 'sqlstate': '42501', 'stage': 'session insert',
         'message': 'row-level security policy for table "[identifier]"', 'role': 'delivery_app',
         'sub_present': False, 'session_id_present': False, 'session_active_for_sub': 'UNKNOWN'}


def _run_start_failure(tmp_path, monkeypatch, block,store):
    task_id='start-'+uuid4().hex
    store._fixture_task_id=task_id
    store.create_task('t1', task_id=task_id)
    session_id=str(uuid4())
    store.begin_interview_start(store._fixture_task_id, session_id, 'route')
    error = ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR,
                          detail='engine program failed during start: InsufficientPrivilege', retryable=True)

    class FailingEngine:
        def start(self, request):
            raise EngineClientError(503, error, block)

    class NoWatch:
        @staticmethod
        async def advance(*args, **kwargs):
            return None

    monkeypatch.setattr(module, 'store', store)
    monkeypatch.setattr(module, 'engine_client', FailingEngine())
    monkeypatch.setattr(module, '_watch_engine_advance', NoWatch.advance)
    asyncio.run(module._complete_interview_start(store._fixture_task_id, session_id,
                                                 'route', None, 'idea', {}, 'owner'))
    return store


def test_start_failure_records_operator_block(tmp_path, monkeypatch,scoped_start_store):
    store = _run_start_failure(tmp_path, monkeypatch, BLOCK,scoped_start_store)
    rows = store.failure_diagnostics(store._fixture_task_id)
    assert len(rows) == 1
    assert rows[0]['sqlstate'] == '42501' and rows[0]['stage'] == 'session insert'
    assert rows[0]['source'] == 'engine.start' and rows[0]['http_status'] == 503


def test_start_failure_customer_events_stay_minimal(tmp_path, monkeypatch,scoped_start_store):
    store = _run_start_failure(tmp_path, monkeypatch, BLOCK,scoped_start_store)
    events = store.events(store._fixture_task_id)
    assert events, 'the failure event itself must still be recorded'
    blob = str(events)
    assert 'operator_diagnostics' not in blob
    assert '42501' not in blob and 'delivery_app' not in blob


def test_start_failure_without_block_records_nothing(tmp_path, monkeypatch,scoped_start_store):
    store = _run_start_failure(tmp_path, monkeypatch, None,scoped_start_store)
    assert store.failure_diagnostics(store._fixture_task_id) == []
    assert store.events(store._fixture_task_id), 'the failure event is still recorded'


from uuid import uuid4
import pytest

@pytest.fixture
def scoped_start_store(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as w,tenant_context(w.scope):yield w.store


def test_real_start_failure_publishes_failed_state_before_error_wakes_stream(monkeypatch,scoped_start_store):
    """Actual async Start completion on migrated PG, not a mocked watcher."""
    import threading
    from delivery_api import event_bus
    store=scoped_start_store;tid=store.create_task('child fails')['task_id'];sid=str(uuid4())
    store.begin_interview_start(tid,sid,'deepseek')
    loop_thread=threading.get_ident();write_threads=[];notified=[]
    original=store.add_event
    def add(*args,**kwargs):
        write_threads.append(threading.get_ident());return original(*args,**kwargs)
    monkeypatch.setattr(store,'add_event',add)
    notify=event_bus.notify
    def inspect(task):
        if task==tid:notified.append(store.task(tid)['state'])
        notify(task)
    monkeypatch.setattr(event_bus,'notify',inspect)
    class Failed:
        def start(self,request):raise RuntimeError('fixture provider rejected HTTP415')
        def progress(self,*args):raise KeyError(sid)
    monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',Failed())
    asyncio.run(module._complete_interview_start(tid,sid,'deepseek',None,'test',{},'owner'))
    assert store.task(tid)['state']=='failed'
    assert notified and all(state=='failed' for state in notified)
    assert write_threads and all(t!=loop_thread for t in write_threads)
    assert any('HTTP415' in e['message'] for e in store.events(tid))
