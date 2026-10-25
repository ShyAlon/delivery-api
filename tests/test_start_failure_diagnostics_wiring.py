"""The session-start failure path records the engine's operator block on the
dedicated diagnostics store while the customer event text stays minimal."""
import asyncio
from types import SimpleNamespace as NS

import delivery_api.app as module
from delivery_api.engine_client import EngineClientError
from delivery_api.store import create_store
from vmodel_contracts.interactive import ProtocolError, ProtocolErrorCode

BLOCK = {'error_class': 'InsufficientPrivilege', 'sqlstate': '42501', 'stage': 'session insert',
         'message': 'row-level security policy for table "[identifier]"', 'role': 'delivery_app',
         'sub_present': False, 'session_id_present': False, 'session_active_for_sub': 'UNKNOWN'}


def _run_start_failure(tmp_path, monkeypatch, block):
    store = create_store(str(tmp_path / 'test.db'))
    store.create_task('t1', task_id='task1')
    store.begin_interview_start('task1', '00000000-0000-0000-0000-0000000000aa', 'route')
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
    asyncio.run(module._complete_interview_start('task1', '00000000-0000-0000-0000-0000000000aa',
                                                 'route', None, 'idea', {}, 'owner'))
    return store


def test_start_failure_records_operator_block(tmp_path, monkeypatch):
    store = _run_start_failure(tmp_path, monkeypatch, BLOCK)
    rows = store.failure_diagnostics('task1')
    assert len(rows) == 1
    assert rows[0]['sqlstate'] == '42501' and rows[0]['stage'] == 'session insert'
    assert rows[0]['source'] == 'engine.start' and rows[0]['http_status'] == 503


def test_start_failure_customer_events_stay_minimal(tmp_path, monkeypatch):
    store = _run_start_failure(tmp_path, monkeypatch, BLOCK)
    events = store.events('task1')
    assert events, 'the failure event itself must still be recorded'
    blob = str(events)
    assert 'operator_diagnostics' not in blob
    assert '42501' not in blob and 'delivery_app' not in blob


def test_start_failure_without_block_records_nothing(tmp_path, monkeypatch):
    store = _run_start_failure(tmp_path, monkeypatch, None)
    assert store.failure_diagnostics('task1') == []
    assert store.events('task1'), 'the failure event is still recorded'
