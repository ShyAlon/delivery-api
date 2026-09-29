"""Task projections must not interleave on the shared SQLite connection."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from delivery_api.store import SQLiteStore


@pytest.mark.parametrize("first,second", [("task", "task"), ("task", "list_tasks"),
                                        ("list_tasks", "task"), ("list_tasks", "list_tasks")])
def test_task_projection_holds_store_lock_through_all_queries(tmp_path, monkeypatch, first, second):
    store = SQLiteStore(str(tmp_path / "read-lock.db"))
    task_id = store.create_task("Lock test")["task_id"]
    first_inside = Event()
    release_first = Event()
    second_attempted = Event()
    second_inside = Event()
    writer_attempted = Event()
    writer_done = Event()
    count_lock = Lock()
    calls = 0
    original = store._task_record

    def paused_projection(row):
        nonlocal calls
        with count_lock:
            calls += 1
            ordinal = calls
        if ordinal == 1:
            first_inside.set()
            if not release_first.wait(3):
                raise TimeoutError("first projection not released")
        else:
            second_inside.set()
        return original(row)

    monkeypatch.setattr(store, "_task_record", paused_projection)

    def read(which):
        return store.task(task_id) if which == "task" else store.list_tasks()

    def second_read():
        second_attempted.set()
        return read(second)

    def write():
        writer_attempted.set()
        store.set_model_default(task_id, "deepseek:deepseek-flash")
        writer_done.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        initial = pool.submit(read, first)
        assert first_inside.wait(2)
        concurrent = pool.submit(second_read)
        writer = pool.submit(write)
        try:
            assert second_attempted.wait(2) and writer_attempted.wait(2)
            # Both the next read and an already-locked write must wait until
            # the first read finishes its phases, gates, and artifact count.
            assert not second_inside.wait(.1)
            assert not writer_done.is_set()
        finally:
            release_first.set()
        assert initial.result(timeout=3)
        assert concurrent.result(timeout=3)
        writer.result(timeout=3)
        assert writer_done.is_set()
        assert store.task(task_id)["model_default"] == "deepseek:deepseek-flash"
