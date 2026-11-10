"""2026-11-10: loop.lag said the loop stalled 1-2.6 s around answers but not on what. The watchdog names the blocked frame."""
import asyncio
import threading
import time

from delivery_api.loop_stall import StallWatchdog


def blocking_work_inside_the_loop():
    time.sleep(0.9)


def test_watchdog_names_the_function_that_blocked_the_loop():
    seen = []

    async def main():
        dog = StallWatchdog(lambda late, stack: seen.append((late, stack)), threading.get_ident(), stall_ms=300, poll_s=0.05).start()
        try:
            for _ in range(3):
                await asyncio.sleep(0.05)
                dog.beat()
            blocking_work_inside_the_loop()
            await asyncio.sleep(0.05)
            dog.beat()
            await asyncio.sleep(0.2)
        finally:
            dog.stop()

    asyncio.run(main())
    assert len(seen) == 1, seen
    late, stack = seen[0]
    assert late >= 300 and 'blocking_work_inside_the_loop' in stack, seen


def test_a_responsive_loop_reports_nothing():
    seen = []

    async def main():
        dog = StallWatchdog(lambda late, stack: seen.append(stack), threading.get_ident(), stall_ms=300, poll_s=0.05).start()
        try:
            for _ in range(10):
                await asyncio.sleep(0.05)
                dog.beat()
        finally:
            dog.stop()

    asyncio.run(main())
    assert seen == []


def test_the_app_monitor_starts_and_stops_its_watchdog_and_logs_a_stack_for_a_stall(monkeypatch):
    from delivery_api import app as app_module, observability
    events = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **fields: events.append((kind, fields)))

    async def main():
        task = asyncio.ensure_future(app_module._loop_lag_monitor())
        await asyncio.sleep(0.3)
        blocking_work_inside_the_loop()
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    stacks = [f for k, f in events if k == 'loop.stall.stack']
    assert len(stacks) == 1 and 'blocking_work_inside_the_loop' in stacks[0]['stack'], events
    assert any(k == 'loop.lag' for k, _ in events)
    assert not [t for t in threading.enumerate() if t.name == 'loop-stall-watchdog' and t.is_alive()]
