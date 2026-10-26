"""A task read is ONE statement: the task row, its phases, gates and artifact
count arrive together. On the remote pooler every extra statement is a full
round trip, and the old _task_record fan-out cost 4 statements per task read
(plus 3 per extra task in list_tasks)."""
import json
import sqlite3

import pytest

from delivery_api.store import SQLiteStore, PHASES


class CountingConnection:
    def __init__(self):
        self.inner = sqlite3.connect(':memory:', check_same_thread=False)
        self.inner.row_factory = sqlite3.Row
        self.selects = 0

    def execute(self, sql, *args):
        if sql.lstrip().upper().startswith('SELECT'):
            self.selects += 1
        return self.inner.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def make_store():
    connection = CountingConnection()
    return SQLiteStore(':memory:', connection=connection), connection


def test_task_read_is_one_statement_with_the_full_record():
    store, connection = make_store()
    task = store.create_task('demo task')
    store._db.execute(
        "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at) VALUES (?,?,?,?,?,?)",
        (task['task_id'], 1, 'intent', 'intent.json', 'ref-1', 1.0))
    store._db.execute(
        "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,stale) VALUES (?,?,?,?,?,?,1)",
        (task['task_id'], 1, 'intent', 'old.json', 'ref-0', 0.5))

    connection.selects = 0
    record = store.task(task['task_id'])

    assert connection.selects == 1
    assert [p['phase_key'] for p in record['phases']] == [key for key, _ in PHASES]
    assert [p['position'] for p in record['phases']] == list(range(len(PHASES)))
    assert all(p['state'] == 'pending' for p in record['phases'])
    assert [g['position'] for g in record['gates']] == list(range(len(PHASES)))
    assert all(isinstance(g['options'], list) for g in record['gates'])
    assert record['artifact_count'] == 1  # the stale one does not count


def test_list_tasks_is_one_statement_regardless_of_task_count():
    store, connection = make_store()
    for i in range(3):
        store.create_task('task %d' % i)

    connection.selects = 0
    tasks = store.list_tasks()

    assert connection.selects == 1
    assert len(tasks) == 3
    assert all(len(t['phases']) == len(PHASES) and len(t['gates']) == len(PHASES) for t in tasks)


def test_recorded_engine_session_reuses_the_request_read(monkeypatch):
    from delivery_api import app
    def boom(task_id):
        raise AssertionError('engine_session read twice in one request')
    monkeypatch.setattr(app.store, 'engine_session', boom)
    assert app._recorded_engine_session('t1', recorded='sess-1') == 'sess-1'


def test_recorded_engine_session_legacy_scan_reuses_request_events(monkeypatch):
    from delivery_api import app
    monkeypatch.setattr(app.store, 'engine_session', lambda task_id: None)
    monkeypatch.setattr(app.store, 'events', lambda task_id: (_ for _ in ()).throw(AssertionError('events read twice in one request')))
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', False)
    events = [{'message': 'interview started session: legacy-9', 'payload': {}}]
    assert app._recorded_engine_session('t1', recorded=None, events=events) == 'legacy-9'
