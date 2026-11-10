"""One watcher cycle is one database statement (2026-11-10).

The debug ZIP showed the watcher paying 3 transactions per cycle (task, session binding, sign-in), each with its own
identity batch and commit. Same checks, one statement."""
import asyncio
import uuid

import pytest

from delivery_api import stop_watch


class CountingStore:
    def __init__(self):
        self.calls = []
    def task(self, *a): self.calls.append('task'); return {'stop_requested_at': None}
    def session_binding(self, *a): self.calls.append('binding'); return {'generation': 3}
    def auth_session_active(self): self.calls.append('auth'); return True
    def stop_state(self, *a):
        self.calls.append('stop_state')
        return {'task_present': True, 'stop_requested_at': None, 'session_generation': 3, 'active': True}


def test_one_cycle_is_one_store_call(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 0.05)
    store = CountingStore()
    async def go():
        stop = asyncio.Event()
        w = asyncio.create_task(stop_watch.watch_for_stop(store=store, task_id='t', session_id='s', generation=3, stop=stop))
        await asyncio.sleep(0.01)
        w.cancel()
        try: await w
        except asyncio.CancelledError: pass
    asyncio.run(go())
    assert store.calls and set(store.calls) == {'stop_state'}


@pytest.mark.parametrize('state', [
    {'task_present': False, 'stop_requested_at': None, 'session_generation': 3, 'active': True},
    {'task_present': True, 'stop_requested_at': 1.0, 'session_generation': 3, 'active': True},
    {'task_present': True, 'stop_requested_at': None, 'session_generation': 4, 'active': True},
    {'task_present': True, 'stop_requested_at': None, 'session_generation': None, 'active': True},
    {'task_present': True, 'stop_requested_at': None, 'session_generation': 3, 'active': False}])
def test_every_stop_reason_still_stops_the_child(state):
    store = CountingStore(); store.stop_state = lambda *a: state
    async def go():
        stop = asyncio.Event()
        await asyncio.wait_for(stop_watch.watch_for_stop(store=store, task_id='t', session_id='s', generation=3, stop=stop), 2)
        return stop.is_set()
    assert asyncio.run(go())


from test_postgres_store import store, A  # noqa: E402  (isolated Postgres fixture)
from delivery_api.tenancy import tenant_context  # noqa: E402


def test_stop_state_is_one_statement_and_matches_the_three_reads(store, monkeypatch):
    with tenant_context(A):
        tid = store.create_task('stopstate-' + uuid.uuid4().hex)['task_id']
        sid = 'ss-' + uuid.uuid4().hex
        store.set_engine_session(tid, sid)
        calls = []; real = store._execute_native
        monkeypatch.setattr(store, '_execute_native', lambda sql, p=(), **kw: (calls.append(sql), real(sql, p, **kw))[1])
        st = store.stop_state(tid, sid)
        assert len(calls) == 1
        binding = store.session_binding(sid)
        assert st == {'task_present': True, 'stop_requested_at': None, 'session_generation': binding['generation'],
                      'active': store.auth_session_active()}
        assert store.stop_state(tid, 'no-such-session')['session_generation'] is None
        store.request_task_stop(tid)
        assert store.stop_state(tid, sid)['stop_requested_at'] is not None
        store.delete_task(tid)
        assert store.stop_state(tid, sid)['task_present'] is False
