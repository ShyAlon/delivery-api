"""The progress stream stops polling the remote database every 2 seconds.

2026-10-27: the stream's synchronous store.events read ran ON the event loop
every 2s per open stream; at ~650ms remote round trip each cycle stalled every
in-flight request. The stream now reads off the loop, wakes on event writes
through this process, and falls back to one safety read a minute."""
import asyncio
import threading

import pytest


def test_stream_events_reads_run_off_the_event_loop(monkeypatch, tmp_path, pg_scoped_workspace):
    from delivery_api import app as app_module
    store = pg_scoped_workspace.store
    task_id = store.create_task('Off loop reads', '')['task_id']
    monkeypatch.setattr(app_module, 'store', store)
    loop_thread = threading.get_ident()  # asyncio.run below drives the loop on this thread
    reads = []
    real_events = store.events
    def spied(task, *args, **kwargs):
        reads.append(threading.get_ident())
        return real_events(task, *args, **kwargs)
    monkeypatch.setattr(store, 'events', spied)

    async def read():
        class Connected:
            async def is_disconnected(self):
                return False
        response = await app_module.task_stream(task_id, Connected())
        iterator = response.body_iterator
        await anext(iterator)   # first cycle output (heartbeat)
        await iterator.aclose()
    asyncio.run(read())
    assert reads, 'the stream never read events'
    assert all(t != loop_thread for t in reads), 'a stream DB read ran on the event loop thread'


def test_bus_wait_wakes_on_notify_and_times_out():
    from delivery_api import event_bus
    assert event_bus.wait('no-such-task', 0.05) is False
    woke = []
    import threading as _t
    waiter = _t.Thread(target=lambda: woke.append(event_bus.wait('t-wake', 2.0)))
    waiter.start()
    event_bus.notify('t-wake')
    waiter.join(2.0)
    assert woke == [True]


def test_add_event_wakes_the_stream_without_the_safety_wait(monkeypatch, tmp_path, pg_scoped_workspace):
    """With heartbeats and the safety refresh effectively disabled, a chunk can only
    arrive because the write woke the stream."""
    from delivery_api import app as app_module
    store = pg_scoped_workspace.store
    task_id = store.create_task('Wake on write', '')['task_id']
    monkeypatch.setattr(app_module, 'store', store)
    monkeypatch.setattr(app_module, 'STREAM_HEARTBEAT_SECONDS', 3600.0)
    monkeypatch.setattr(app_module, 'STREAM_SAFETY_SECONDS', 7200.0)

    async def read():
        class Connected:
            async def is_disconnected(self):
                return False
        response = await app_module.task_stream(task_id, Connected())
        iterator = response.body_iterator
        await anext(iterator)  # initial cycle output
        store.add_event(task_id, 'progress', 'woken by the write')
        chunk = await asyncio.wait_for(anext(iterator), 3)
        assert 'woken by the write' in chunk
        await iterator.aclose()
    asyncio.run(read())


def test_stream_reads_only_on_writes_and_the_minute_safety(monkeypatch, tmp_path, pg_scoped_workspace):
    from delivery_api import app as app_module
    store = pg_scoped_workspace.store
    task_id = store.create_task('Safety cadence', '')['task_id']
    monkeypatch.setattr(app_module, 'store', store)
    monkeypatch.setattr(app_module, 'STREAM_HEARTBEAT_SECONDS', 0.05)
    monkeypatch.setattr(app_module, 'STREAM_SAFETY_SECONDS', 0.21)
    reads = []
    real_events = store.events
    def spied(task, *args, **kwargs):
        reads.append(1)
        return real_events(task, *args, **kwargs)
    monkeypatch.setattr(store, 'events', spied)

    async def read():
        class Connected:
            async def is_disconnected(self):
                return False
        response = await app_module.task_stream(task_id, Connected())
        iterator = response.body_iterator
        heartbeats = 0
        deadline = asyncio.get_running_loop().time() + 0.55
        while asyncio.get_running_loop().time() < deadline:
            chunk = await asyncio.wait_for(anext(iterator), 2)
            if 'event: heartbeat' in chunk:
                heartbeats += 1
        await iterator.aclose()
        return heartbeats
    heartbeats = asyncio.run(read())
    # 1 initial read + safety reads ~every 0.21s; heartbeats flow every 0.05s with no reads.
    assert len(reads) <= 4, f'the stream polled the database {len(reads)} times in 0.55s'
    assert len(reads) >= 2, 'the safety refresh never read'
    assert heartbeats >= 6, f'only {heartbeats} heartbeats flowed'


def test_session_check_is_cached_per_session_and_expires():
    from delivery_api.tenant_http import SessionCheckCache
    calls = []
    cache = SessionCheckCache(lambda: calls.append(1) or True, ttl=30.0)
    clock = [1000.0]
    cache._time = lambda: clock[0]
    assert cache.active('owner-1','sess-1') is True and cache.active('owner-1','sess-1') is True
    assert len(calls) == 1, 'a second request in the TTL window re-checked the session'
    assert cache.active('owner-1','sess-2') is True
    assert len(calls) == 2, 'a different session shared the first session\'s result'
    clock[0] += 31.0
    assert cache.active('owner-1','sess-1') is True
    assert len(calls) == 3, 'an expired entry was still served'


def test_session_check_cache_stores_revocation_and_never_caches_errors():
    from delivery_api.tenant_http import SessionCheckCache
    calls = []
    def check():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('database unreachable')
        return False
    cache = SessionCheckCache(check, ttl=30.0)
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        cache.active('owner-1','sess-1')
    assert cache.active('owner-1','sess-1') is False and cache.active('owner-1','sess-1') is False
    assert len(calls) == 2, 'an error was cached, or revocation was not'


def test_notify_fires_only_after_the_row_is_committed(tmp_path, pg_scoped_workspace):
    """A stream woken by the write must see the row: notify happens after commit."""
    import threading as _t
    from delivery_api import event_bus
    store = pg_scoped_workspace.store
    task_id = store.create_task('Commit order', '')['task_id']
    seen = []
    def waiter():
        from delivery_api.tenancy import tenant_context
        with tenant_context(pg_scoped_workspace.scope):
            if event_bus.wait(task_id, 2.0):
                seen.append(any('committed event' in e['message'] for e in store.events(task_id)))
    thread = _t.Thread(target=waiter)
    thread.start()
    import time as _time
    _time.sleep(0.05)
    store.add_event(task_id, 'progress', 'committed event')
    thread.join(2.0)
    assert seen == [True], 'the wake fired before the row was readable, or never fired'


needs_node = __import__('pytest').mark.skipif(__import__('shutil').which('node') is None, reason='needs node')


@needs_node
def test_review_progress_poll_backs_off_while_the_stream_is_open():
    """The review screen polls once a minute while the stream is open, instantly on
    stream events, and at 1.5s again when the stream dies."""
    import subprocess
    from pathlib import Path
    uri = (Path(__file__).parent.parent / 'src' / 'delivery_api' / 'web' / 'progress.js').as_uri()
    src = """
globalThis.window={addEventListener(){}};globalThis.document={addEventListener(){}};
const m=await import('%s');
const M=m.REVIEW_POLL_STREAM_MS;
if(M!==60000)throw new Error('stream-open cadence must be 60s, got '+M);
if(m.shouldPollNow(true,100000,100000+59999))throw new Error('polled inside the stream-open window');
if(!m.shouldPollNow(true,100000,100000+60000))throw new Error('skipped the once-a-minute safety poll');
if(!m.shouldPollNow(false,100000,100000))throw new Error('a dead stream must poll at full cadence');
let esUrl=null,polls=0;
globalThis.EventSource=class{constructor(url){esUrl=url;setTimeout(()=>this.onopen&&this.onopen(),0)}addEventListener(k,f){if(k==='progress')this.onprogress=f}close(){this.closed=true}};
globalThis.fetch=async()=>{polls++;return {ok:true,json:async()=>({events:[{id:1,kind:'progress',message:'working',payload:{}}]})}};
const host={isConnected:true,querySelector(){return null},set innerHTML(v){},get innerHTML(){return ''}};
const stop=m.watchReviewProgress('task-9',host);
await new Promise(r=>setTimeout(r,80));
if(esUrl!=='/api/tasks/task-9/stream')throw new Error('no stream opened: '+esUrl);
if(polls!==1)throw new Error('expected exactly the initial poll, got '+polls);
await new Promise(r=>setTimeout(r,3200));
if(polls!==1)throw new Error('stream-open polling did not back off: '+polls);
stop();
console.log('ok');
""" % uri
    out = subprocess.run(['node', '--input-type=module', '-e', src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip() == 'ok', out.stderr or out.stdout


def test_stream_fixture_is_restricted_pg(pg_scoped_workspace):
    from delivery_api.postgres_store import PostgresStore
    from pg_storage_guard import assert_pg_storage_only
    assert isinstance(pg_scoped_workspace.store,PostgresStore)
    assert_pg_storage_only()
    with pg_scoped_workspace.store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'
