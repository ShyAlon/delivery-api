"""2026-11-10: one 153 s answer read the progress cursor from the database 28 times (5 round trips each).
Owner-approved option B: the watcher keeps the cursor in memory, re-reading at start, every 30 s and after an error."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from delivery_api import app as module


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class Store:
    def __init__(self):
        self.reads = 0
        self.deleted = False

    def progress_cursor(self, *args):
        self.reads += 1
        if self.deleted:
            raise KeyError('t')
        return 7


class Engine:
    def __init__(self):
        self.asked = []
        self.events_at = set()

    def progress(self, session, cursor):
        self.asked.append(cursor)
        if cursor in self.events_at:
            return SimpleNamespace(events=[SimpleNamespace(timestamp=datetime.now(timezone.utc))], next_cursor=cursor + 1)
        return SimpleNamespace(events=[], next_cursor=cursor)


@pytest.fixture
def env(monkeypatch):
    store, engine, clock = Store(), Engine(), Clock()
    monkeypatch.setattr(module, 'store', store)
    monkeypatch.setattr(module, 'engine_client', engine)
    monkeypatch.setattr('delivery_api.progress_adapter.ingest_interview_page', lambda *a: None)
    return store, engine, module._ProgressCursor(clock), clock


def drain(cache):
    return asyncio.run(module._drain_engine_progress('t', 's', allow_absent=True, cursor_cache=cache))


def test_many_drains_read_the_database_cursor_once(env):
    store, engine, cache, clock = env
    for _ in range(28):
        drain(cache)
    assert store.reads == 1 and engine.asked == [7] * 28


def test_the_cursor_advances_from_ingested_pages_without_a_database_read(env):
    store, engine, cache, clock = env
    engine.events_at = {7, 8}
    drain(cache)
    assert store.reads == 1 and engine.asked == [7, 8, 9] and cache.value == 9
    drain(cache)
    assert store.reads == 1 and engine.asked[-1] == 9


def test_cursor_is_re_read_after_30_seconds(env):
    store, engine, cache, clock = env
    drain(cache)
    clock.now += 29
    drain(cache)
    assert store.reads == 1
    clock.now += 2
    drain(cache)
    assert store.reads == 2


def test_cursor_is_re_read_after_an_error(env):
    store, engine, cache, clock = env
    drain(cache)
    cache.clear()
    drain(cache)
    assert store.reads == 2


def test_a_deleted_task_is_noticed_at_the_next_refresh(env):
    store, engine, cache, clock = env
    drain(cache)
    store.deleted = True
    drain(cache)   # still within 30 s: bookkeeping only, no authority decision here
    clock.now += 31
    with pytest.raises(KeyError):
        drain(cache)


def test_without_a_cache_every_drain_still_reads_the_cursor(env):
    store, engine, cache, clock = env
    for _ in range(3):
        drain(None)
    assert store.reads == 3


def test_a_single_drain_without_explicit_cache_does_not_reread_each_page(env):
    store,engine,cache,clock=env
    engine.events_at={7,8}
    drain(None)
    assert store.reads==1 and engine.asked==[7,8,9]
