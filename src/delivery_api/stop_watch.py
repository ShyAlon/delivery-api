"""Stop / revocation watch for a running child answer.

Replaces the 5 s poll (2026-11-10 trace: about 21 cycles x 15 round trips per ~100 s answer).
A Stop click wakes the watcher at once (the stop request commits through this process and
notifies the event bus). Revocation, a session generation change, or a Stop made in another
process is caught by a safety read every STOP_SAFETY_SECONDS. Each read is the same three
checks as before; only their cadence changed.

Single-process wake, like event_bus (see its module note): a second server would not wake
this watcher, and would be noticed at the next safety read."""
from __future__ import annotations

import asyncio
import threading

from . import event_bus

STOP_SAFETY_SECONDS = 30.0


def stop_key(task_id):
    return 'task-stop:' + task_id


async def watch_for_stop(*, store, task_id, session_id, generation, stop):
    key = stop_key(task_id)
    gone = threading.Event()   # set on exit so a waiting worker thread ends promptly
    try:
        while not stop.is_set():
            version = event_bus.version(key)   # before the reads: a Stop landing between read and wait still wakes
            task = store.task(task_id)
            session = store.session_binding(session_id)
            if (not task or task.get('stop_requested_at') is not None or not store.auth_session_active()
                    or not session or session['generation'] != generation):
                stop.set()
                return
            await asyncio.to_thread(event_bus.wait, key, STOP_SAFETY_SECONDS, gone, version)
    finally:
        gone.set()
