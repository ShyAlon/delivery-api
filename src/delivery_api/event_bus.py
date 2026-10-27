"""In-process wake bus for progress events: a write through this process wakes the
progress streams for that task, so they read the database on change instead of on a
timer. One safety refresh per minute bounds a lost wakeup.

Single-process by design: every event writer in the local execution preview lives in
this uvicorn process (the engine is hosted in-process). A multi-worker cloud deploy
must replace this with Postgres LISTEN/NOTIFY - named here so the assumption is
visible, per the 2026-10-27 design ruling."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_watchers: dict[str, set[threading.Event]] = {}
_versions: dict[str, int] = {}


def notify(task_id: str) -> None:
    """An event row for task_id was committed through this process. Wake its streams."""
    with _lock:
        _versions[task_id] = _versions.get(task_id, 0) + 1
        watchers = list(_watchers.get(task_id, ()))
    for woke in watchers:
        woke.set()


def version(task_id: str) -> int:
    """The write generation of task_id. A reader snapshots it BEFORE reading, so a
    write that lands between the read and the next wait still wakes the reader."""
    with _lock:
        return _versions.get(task_id, 0)


def wait(task_id: str, seconds: float, abort: threading.Event | None = None, since: int | None = None) -> bool:
    """Block up to `seconds` for the next write to task_id. True when a write woke us.
    `abort` (any object with is_set(), e.g. the stream's shutdown event) ends the wait
    promptly without it counting as a write."""
    woke = threading.Event()
    with _lock:
        if since is not None and _versions.get(task_id, 0) != since:
            return True
        _watchers.setdefault(task_id, set()).add(woke)
    try:
        slice_ = min(0.2, seconds)
        while not woke.wait(slice_):
            if abort is not None and abort.is_set():
                return False
            seconds -= slice_
            if seconds <= 0:
                return False
            slice_ = min(0.2, seconds)
        return True
    finally:
        with _lock:
            remaining = _watchers.get(task_id)
            if remaining is not None:
                remaining.discard(woke)
                if not remaining:
                    del _watchers[task_id]
