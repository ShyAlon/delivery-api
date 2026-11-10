"""The 2026-11-10 ZIP left 28 syncs under "unattributed[thread=AnyIO worker thread]", logged 80 of 145 purposes, and the one
remaining 437 ms loop stall showed only library frames. Name the code that asked."""
import asyncio
import importlib.util
import threading
import time

from delivery_api import observability, postgres_scope, request_costs, statement_trace
from delivery_api.loop_stall import StallWatchdog


def some_worker_job():
    statement_trace.record('statement', 'SELECT 1', 0.0)


def test_an_unattributed_statement_names_the_code_that_asked():
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        import contextvars
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(some_worker_job), name='AnyIO worker thread')
        t.start(); t.join()
    finally:
        postgres_scope.request_stats.reset(token)
    where = stats.trace[0][0]
    assert where.startswith('unattributed[thread=AnyIO worker thread') and 'some_worker_job' in where, where


def test_every_purpose_of_a_busy_request_is_logged(monkeypatch):
    seen = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **f: seen.append((kind, f)))
    stats = postgres_scope.RequestStats()
    stats.trace = [('app:purpose_%d' % i, 'SELECT 1', 1, 1) for i in range(200)]   # the old cut kept 80
    statement_trace.fold_request('POST /api/tasks/<id>/answer', stats)
    assert len([1 for k, _ in seen if k == 'db.purpose_row']) == 200
    budget = next(f for k, f in seen if k == 'db.answer_budget')
    assert budget['purposes_logged'] == budget['purposes_total'] == 200


def test_a_stall_stack_names_the_delivery_code_that_asked_not_only_the_library_frames(tmp_path):
    pkg = tmp_path / 'delivery_api'
    pkg.mkdir()
    (pkg / 'asker.py').write_text(
        'import contextlib, time\n'
        'def lib_a(): time.sleep(0.9)\n'
        'def lib_b(): lib_a()\n'
        'def lib_c(): lib_b()\n'
        'def lib_d(): lib_c()\n'
        'def lib_e(): lib_d()\n'
        'def handler_that_blocks_the_loop(): lib_e()\n')
    spec = importlib.util.spec_from_file_location('asker', pkg / 'asker.py')
    asker = importlib.util.module_from_spec(spec); spec.loader.exec_module(asker)
    seen = []

    async def main():
        dog = StallWatchdog(lambda late, stack: seen.append(stack), threading.get_ident(), stall_ms=300, poll_s=0.05).start()
        try:
            for _ in range(3):
                await asyncio.sleep(0.05); dog.beat()
            asker.handler_that_blocks_the_loop()
            await asyncio.sleep(0.05); dog.beat(); await asyncio.sleep(0.2)
        finally:
            dog.stop()

    asyncio.run(main())
    assert len(seen) == 1 and 'handler_that_blocks_the_loop' in seen[0] and 'asked by:' in seen[0], seen
    assert len(seen[0]) < 500, 'the log keeps 500 characters per field: %d' % len(seen[0])
