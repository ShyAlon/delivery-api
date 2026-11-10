"""Stop reaches a running answer at once; revocation is read every 30 s, not every 5 s (2026-11-10).

The 5 s watcher re-read task + session binding + auth session for the whole ~100 s of an answer
(about 21 cycles x 15 round trips in the trace). Shy's rulings: a Stop click interrupts at once (a
same-process wake, no polling), and revocation / a Stop from another process is read every 30 s."""
import asyncio
import os
import threading
import time
import uuid

import pytest

from delivery_api import event_bus
from delivery_api import stop_watch


class FakeStore:
    def __init__(self, task_id, generation=3):
        self.task_id, self.generation = task_id, generation
        self.stop_requested_at = None
        self.active = True
        self.reads = 0
    def task(self, task_id):
        self.reads += 1
        return {'task_id': task_id, 'stop_requested_at': self.stop_requested_at}
    def session_binding(self, sid):
        return {'generation': self.generation}
    def auth_session_active(self):
        return self.active


def _run(coro):
    return asyncio.run(coro)


async def _watch(store, tid, stop, generation=3):
    return asyncio.create_task(stop_watch.watch_for_stop(store=store, task_id=tid, session_id='s', generation=generation, stop=stop))


def test_safety_interval_is_thirty_seconds():
    assert stop_watch.STOP_SAFETY_SECONDS == 30.0


def test_stop_click_wakes_the_watcher_at_once(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 30.0)
    tid = uuid.uuid4().hex
    store = FakeStore(tid)
    async def go():
        stop = asyncio.Event()
        watcher = await _watch(store, tid, stop)
        await asyncio.sleep(0.4)
        assert not stop.is_set() and store.reads == 1, 'idle watcher must not poll'
        store.stop_requested_at = time.time()
        event_bus.notify(stop_watch.stop_key(tid))
        await asyncio.wait_for(stop.wait(), 2)
        await watcher
    started = time.monotonic()
    _run(go())
    assert time.monotonic() - started < 3


def test_idle_watcher_reads_only_on_the_safety_interval(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 0.5)
    tid = uuid.uuid4().hex
    store = FakeStore(tid)
    async def go():
        stop = asyncio.Event()
        watcher = await _watch(store, tid, stop)
        await asyncio.sleep(1.3)
        watcher.cancel()
        try:await watcher
        except asyncio.CancelledError:pass
    _run(go())
    assert 2 <= store.reads <= 4, store.reads


def test_revocation_is_noticed_on_the_safety_read(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 0.3)
    tid = uuid.uuid4().hex
    store = FakeStore(tid)
    async def go():
        stop = asyncio.Event()
        watcher = await _watch(store, tid, stop)
        await asyncio.sleep(0.1)
        store.active = False
        await asyncio.wait_for(stop.wait(), 2)
        await watcher
    _run(go())


def test_generation_change_and_missing_task_stop_the_child(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 0.3)
    for mutate in (lambda s: setattr(s, 'generation', 4), lambda s: setattr(s, 'task', lambda tid: None)):
        tid = uuid.uuid4().hex
        store = FakeStore(tid)
        async def go():
            stop = asyncio.Event()
            watcher = await _watch(store, tid, stop)
            await asyncio.sleep(0.1)
            mutate(store)
            await asyncio.wait_for(stop.wait(), 2)
            await watcher
        _run(go())


def test_cancelled_watcher_leaves_no_waiting_thread(monkeypatch):
    monkeypatch.setattr(stop_watch, 'STOP_SAFETY_SECONDS', 30.0)
    tid = uuid.uuid4().hex
    store = FakeStore(tid)
    before = threading.active_count()
    async def go():
        stop = asyncio.Event()
        watcher = await _watch(store, tid, stop)
        await asyncio.sleep(0.3)
        watcher.cancel()
        try:await watcher
        except asyncio.CancelledError:pass
        await asyncio.sleep(0.6)
    _run(go())
    time.sleep(0.5)
    assert threading.active_count() <= before + 1


DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')


@pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
def test_request_task_stop_wakes_the_watcher_after_commit():
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope, tenant_context
    store = PostgresStore(DSN.replace('user=sandbox', 'user=delivery_app'))
    scope = TenantScope('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1')
    with tenant_context(scope):
        tid = store.create_task('stopwake-' + uuid.uuid4().hex)['task_id']
        try:
            key = stop_watch.stop_key(tid)
            before = event_bus.version(key)
            store.request_task_stop(tid)
            assert event_bus.version(key) == before + 1
            store.request_task_stop(tid)   # already requested: no second wake
            assert event_bus.version(key) == before + 1
        finally:
            store.delete_task(tid)
