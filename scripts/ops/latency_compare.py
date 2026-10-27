"""Repeatable before/after latency profile for one checkout: same fixed client
scenario, same metrics, printed as one comparable text table.

Run:  python scripts/ops/latency_compare.py --repo /path/to/checkout
The script imports the app code from --repo, so one copy of it measures any build.
Local numbers are STATEMENT and READ counts plus cadences - wall-clock round trips
only exist against the remote database; compare those from the Debug ZIP fields
per-route p50/p95 (duration/db/auth/checkout/loop_blocked) and the stream row's
loop_blocked_ms/max_cycle_ms/db_cycles before and after the merge.
"""
import argparse, asyncio, json, re, sqlite3, sys, tempfile, time
from pathlib import Path


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


def load_app(repo):
    sys.path.insert(0, str(Path(repo) / 'src'))
    for module in [m for m in list(sys.modules) if m.startswith('delivery_api')]:
        del sys.modules[module]
    import delivery_api.app as app_module
    from delivery_api.store import SQLiteStore
    connection = CountingConnection()
    store = SQLiteStore(':memory:', connection=connection)
    app_module.store = store
    return app_module, store, connection


def declared_idle_cadence_ms(repo):
    text = (Path(repo) / 'src' / 'delivery_api' / 'app.py').read_text()
    safety = re.search(r'STREAM_SAFETY_SECONDS\s*=\s*([\d.]+)', text)
    if safety:
        return int(float(safety.group(1)) * 1000)
    stream = text[text.index('def task_stream'):]
    poll = re.search(r'stop\.wait\(\),(\d+)', stream)
    return int(poll.group(1)) * 1000 if poll else None


def stream_metrics(app_module, store, declared_idle_cadence_ms, seconds=7.0):
    reads = []
    real_events = store.events
    def spied(task, *a, **k):
        reads.append(time.monotonic())
        return real_events(task, *a, **k)
    store.events = spied
    task_id = store.create_task('compare', '')['task_id']
    wake_ms = [None]
    started_at = time.monotonic()
    async def run():
        class Connected:
            async def is_disconnected(self):
                return False
        response = await app_module.task_stream(task_id, Connected())
        iterator = response.body_iterator
        await anext(iterator)
        deadline = time.monotonic() + seconds
        wrote_at = [0.0]
        def probe():
            wrote_at[0] = time.monotonic()
            store.add_event(task_id, 'progress', 'wake probe')
        asyncio.get_running_loop().call_later(5.0, probe)
        while time.monotonic() < deadline:
            try:
                chunk = await asyncio.wait_for(anext(iterator), max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if wrote_at[0] and wake_ms[0] is None and 'wake probe' in chunk:
                wake_ms[0] = int((time.monotonic() - wrote_at[0]) * 1000)
        await iterator.aclose()
    asyncio.run(run())
    store.events = real_events
    # Idle polling: reads after the connect read and before the 5s probe write.
    idle = sum(1 for at in reads if started_at + 0.2 < at < started_at + 4.8)
    return {'stream_idle_db_reads_in_5s': idle,
            'stream_declared_idle_cadence_ms': declared_idle_cadence_ms,
            'stream_wake_to_event_ms': wake_ms[0]}


def browser_poll_cadence_ms(repo):
    text = (Path(repo) / 'src' / 'delivery_api' / 'web' / 'progress.js').read_text()
    stream_open = re.search(r'REVIEW_POLL_STREAM_MS=(\d+)', text)
    interval = re.search(r'setInterval\(\(\)=>poll\(false\),(\d+)\)|setInterval\(poll,(\d+)\)', text)
    fallback = next(int(g) for g in (interval.group(1), interval.group(2)) if g) if interval else None
    return {'browser_events_poll_ms_stream_open': int(stream_open.group(1)) if stream_open else fallback,
            'browser_events_poll_ms_stream_dead': fallback}


def session_check_calls(repo):
    sys.path.insert(0, str(Path(repo) / 'src'))
    from delivery_api import tenant_http
    calls = []
    check = lambda: calls.append(1) or True
    cache_class = getattr(tenant_http, 'SessionCheckCache', None)
    if cache_class is None:
        for _ in range(10):
            check()
        return {'session_checks_per_10_requests': len(calls), 'session_check_cache': 'absent'}
    cache = cache_class(check)
    for _ in range(10):
        cache.active('session-1')
    return {'session_checks_per_10_requests': len(calls), 'session_check_cache': 'present'}


def request_metrics(app_module, store, connection, task_id):
    from fastapi.testclient import TestClient
    client = TestClient(app_module.app)
    rows = []
    for label, path in (('GET /api/tasks/<id>/events', '/api/tasks/%s/events?after=0' % task_id),
                        ('GET /api/tasks/<id>', '/api/tasks/%s' % task_id)):
        connection.selects = 0
        started = time.monotonic()
        response = client.get(path)
        rows.append({'route': label, 'status': response.status_code,
                     'select_statements': connection.selects,
                     'local_ms': int((time.monotonic() - started) * 1000)})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    print('run: python scripts/ops/latency_compare.py --repo %s' % args.repo)
    app_module, store, connection = load_app(args.repo)
    task_id = store.create_task('compare requests', '')['task_id']
    metrics = {}
    metrics.update(browser_poll_cadence_ms(args.repo))
    metrics.update(session_check_calls(args.repo))
    metrics.update(stream_metrics(app_module, store, declared_idle_cadence_ms(args.repo)))
    rows = request_metrics(app_module, store, connection, task_id)
    print('metric                              value')
    for key, value in metrics.items():
        print('%-36s  %s' % (key, value))
    print('route                               status  select_statements  local_ms')
    for row in rows:
        print('%-36s  %-6s  %-17s  %s' % (row['route'], row['status'], row['select_statements'], row['local_ms']))


if __name__ == '__main__':
    main()
