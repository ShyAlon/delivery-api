"""engine session id -> task id, remembered per (store, tenant) for the child supervisor's timing sink.

The sink maps a session id to its task up to 13 times per answer from a background thread, each lookup its own transaction
(~5 round trips on a ~164 ms link) for a mapping that changes only when a task's engine_session_id is set, rebound or the task
is deleted. Single delivery-api process per task (owner-confirmed 2026-11-10): every such write goes through this process and
drops the entry. Never cached: authority (stop, generation, session binding, auth liveness). Only a hit skips the read; a miss,
a None, a writer in the same transaction or a concurrent invalidation always goes to the database.

Race rule: the version is read BEFORE the query and the value is stored only if it is unchanged, so a read that overlapped a
committed write cannot store the old answer."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_entries = {}
_versions = {}
MAX_ENTRIES = 2048
MAP_WRITE_MARKERS = ('engine_session_id', 'deleted_at', 'delete from tasks')


def _scope_key(store, session_id):
    from .tenancy import current_scope
    return (store, current_scope(), session_id)


def version(store):
    with _lock:
        return _versions.get(store, 0)


def invalidate(store):
    with _lock:
        _versions[store] = _versions.get(store, 0) + 1
        for key in [k for k in _entries if k[0] is store]:
            del _entries[key]


def affects_mapping(sql):
    text = ' '.join(str(sql).lower().split())
    return any(marker in text for marker in MAP_WRITE_MARKERS)


def get(store, session_id):
    with _lock:
        return _entries.get(_scope_key(store, session_id))


def put(store, session_id, task_id, *, read_version):
    if task_id is None:
        return
    with _lock:
        if _versions.get(store, 0) != read_version:
            return
        if len(_entries) >= MAX_ENTRIES:
            _entries.pop(next(iter(_entries)))
        _entries[_scope_key(store, session_id)] = task_id


def clear():
    with _lock:
        _entries.clear()
        _versions.clear()
