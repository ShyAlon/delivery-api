"""Shared native connection reads, cursor consumption and sync stay serialized."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from delivery_api.store import SQLiteStore, TursoReplicaStore


class GuardCursor:
    def __init__(self, owner, cursor):
        self.owner, self.cursor = owner, cursor

    def fetchone(self):
        self.owner.check()
        return self.cursor.fetchone()

    def fetchall(self):
        self.owner.check()
        return self.cursor.fetchall()

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class GuardConnection:
    def __init__(self):
        self.inner = sqlite3.connect(':memory:', check_same_thread=False)
        self.inner.row_factory = sqlite3.Row
        self.lock = None
        self.sync_locked = False
        self.fetch_started = self.release_fetch = None

    def check(self):
        if self.lock is not None:
            assert self.lock._is_owned(), 'shared connection used without its store lock'

    def execute(self, sql, *args):
        self.check()
        cursor = GuardCursor(self, self.inner.execute(sql, *args))
        if self.fetch_started and sql.startswith('SELECT cursor FROM progress_ingest'):
            base_fetch = cursor.fetchone
            def held_fetch():
                self.fetch_started.set()
                assert self.release_fetch.wait(5)
                return base_fetch()
            cursor.fetchone = held_fetch
        return cursor

    def executemany(self, *args):
        self.check()
        return GuardCursor(self, self.inner.executemany(*args))

    def executescript(self, *args):
        self.check()
        return self.inner.executescript(*args)

    def commit(self):
        self.check()
        return self.inner.commit()

    def sync(self):
        self.sync_locked = self.lock is not None and self.lock._is_owned()
        self.check()


READS = [
    ('link', ('absent',)), ('run', ('absent',)), ('total_spend', ()),
    ('artifacts', ('absent',)), ('task', ('t',)), ('list_tasks', ()),
    ('engine_session', ('t',)), ('interview_schema_binding', ('t',)),
    ('review_comments', ('t', 'wait', 'revision')),
    ('decision_review', ('t', 'wait')), ('decision_review', ('t', 'wait', 'revision')),
    ('decision_draft', ('t', 'wait', 'revision', 'draft')),
    ('progress_cursor', ('t', 'interview', 'session')), ('events', ('t',)),
    ('audit_entries', ()), ('audit_entries', ('t',)), ('list_feedback', ('t',)),
    ('task_artifacts', ('t',)), ('rerun_prerequisite', ('t', 'intent')),
    ('basic_user', ('absent',)), ('user_quota', ('USER',)),
    ('session', ('absent',)),
]


@pytest.mark.parametrize('method,args', READS)
def test_complete_read_and_cursor_fetch_hold_shared_store_lock(method, args):
    connection = GuardConnection()
    store = SQLiteStore(':memory:', connection=connection)
    store.create_task('Task', task_id='t')
    user = store.upsert_user('basic', 'user', None, 'User', 1000, False)
    args = tuple(user['user_id'] if arg == 'USER' else arg for arg in args)
    connection.lock = store._lock
    getattr(store, method)(*args)


def test_read_fetch_excludes_writer_until_cursor_is_consumed():
    connection = GuardConnection()
    store = SQLiteStore(':memory:', connection=connection)
    store.create_task('Task', task_id='t')
    connection.lock = store._lock
    connection.fetch_started, connection.release_fetch = Event(), Event()
    writer_entered, writer_finished = Event(), Event()
    def write():
        writer_entered.set()
        store.add_event('t', 'test', 'Message')
        writer_finished.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        read = pool.submit(store.progress_cursor, 't', 'interview', 'session')
        assert connection.fetch_started.wait(5)
        writer = pool.submit(write)
        try:
            assert writer_entered.wait(5)
            assert not writer_finished.is_set()
        finally:
            connection.release_fetch.set()
        assert read.result(5) == 0
        writer.result(5)
    assert writer_finished.is_set()


def test_replica_sync_uses_same_store_lock():
    connection = GuardConnection()
    connection.inner.row_factory = None
    store = TursoReplicaStore(':memory:', 'libsql://unused', 'unused', connection=connection)
    connection.lock = store._lock
    store._safe_sync('test')
    assert connection.sync_locked
