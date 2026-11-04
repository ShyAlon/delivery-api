"""In-memory cache of the hot task read (task record + newest events).

A task page that changed nothing costs ZERO statements: the entry is valid
while its event-bus version and store commit epoch are unchanged and the
entry is younger than the safety TTL. Store-wide invalidation is conservative:
an unrelated write may evict it, but a write without an event cannot hide. The TTL
(60s, same cadence as the stream's safety refresh) bounds changes made OUTSIDE
this process - the engine's own writes never touch this bus, so a cross-process
change can show up to 60s stale. Owner accepted that tradeoff 2026-10-27.

Single-process by design, like event_bus itself: a multi-worker deploy must
replace the version check with a shared invalidation channel. Entries are
deep-copied in and out: callers must never mutate shared cache state."""
from __future__ import annotations

import copy
import threading
import time
import weakref

from . import event_bus

SAFETY_TTL_SECONDS = 60

_lock = threading.Lock()
_entries = {}
_epochs = weakref.WeakKeyDictionary()
MAX_ENTRIES = 512


def key(store, task_id):
    from . import config
    from .tenancy import current_scope
    identity = current_scope()
    # The store object itself prevents id reuse and cross-database cache hits.
    return (store, identity, task_id)


def version(store, task_id):
    with _lock:
        epoch = _epochs.get(store, 0)
    return (epoch, event_bus.version(task_id))


def invalidate(store):
    # O(1): version checks reject old entries; bounded put eviction reclaims them.
    with _lock:
        _epochs[store] = _epochs.get(store, 0) + 1


def get(store, task_id):
    k = key(store, task_id)
    with _lock:
        entry = _entries.get(k)
    if entry is None:
        return None
    read_version, stored_at, data = entry
    if version(store, task_id) != read_version or time.monotonic() - stored_at >= SAFETY_TTL_SECONDS:
        return None
    return copy.deepcopy(data)


def put(store, task_id, data, *, read_version):
    with _lock:
        if len(_entries) >= MAX_ENTRIES:
            _entries.pop(next(iter(_entries)))
        _entries[key(store, task_id)] = (read_version, time.monotonic(), copy.deepcopy(data))


def clear():
    with _lock:
        _entries.clear()
        _epochs.clear()
