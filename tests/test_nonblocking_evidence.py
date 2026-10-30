import asyncio,time,threading
import pytest
from delivery_api import postgres_logs

def test_slow_log_storage_does_not_block_event_loop(monkeypatch):
    def slow(*a,**kw):time.sleep(.15)
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',slow)
    async def measure():
        start=time.perf_counter()
        for i in range(3):postgres_logs.emit('activity','fixture')
        return time.perf_counter()-start
    elapsed=asyncio.run(measure())
    print('enqueue_3_seconds',elapsed)
    assert elapsed<.05,'PG evidence must not block the event loop'
    assert postgres_logs.flush(3)


def test_pending_status_is_not_durability_ack(monkeypatch):
    entered=threading.Event();release=threading.Event()
    def blocked(*a,**kw):entered.set();release.wait(2)
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',blocked)
    try:
        assert postgres_logs.emit('activity','queued')
        assert entered.wait(1)
        status=postgres_logs.writer_status()
        assert status['pending']==1 and 'not durable' in status['acknowledgement']
    finally:release.set()
    assert postgres_logs.flush(3)


def test_overflow_is_explicit_degradation(monkeypatch):
    import queue
    from delivery_api import log_durability
    assert postgres_logs.flush(3)
    monkeypatch.setattr(log_durability,'_failures',{})
    def full(*a,**kw):raise queue.Full()
    monkeypatch.setattr(postgres_logs._pending,'put_nowait',full)
    assert postgres_logs.emit('activity','overflow')
    assert log_durability.snapshot()['sources'][0]['category']=='Full'


def test_handler_contention_cannot_stall_loop_or_anonymous_client_log(monkeypatch):
    from delivery_api import observability
    # A slow writer is already busy when the async middleware logs a second line.
    entered=threading.Event();release=threading.Event()
    def blocked(*a,**kw):entered.set();release.wait(2)
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',blocked)
    observability.event('test.slow','fixture',console=False)
    assert entered.wait(1)
    async def run():
        start=time.perf_counter()
        observability.event('http.request','fixture',console=False)
        await asyncio.sleep(.01)
        return time.perf_counter()-start
    try:assert asyncio.run(run())<.1
    finally:release.set()
    assert postgres_logs.flush(3)


def test_enqueue_captures_timestamp_before_delayed_persistence(monkeypatch):
    stamps=[]
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',lambda *a,**kw:stamps.append(kw['ts']))
    before=time.time()
    assert postgres_logs.emit('activity','timestamp')
    assert postgres_logs.flush(3)
    assert before<=stamps[0]<=time.time()
