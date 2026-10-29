"""A task data read is ONE statement: the task row, its phases, gates and artifact
count arrive together. On the remote pooler every extra statement is a full
round trip, and the old _task_record fan-out cost 4 statements per task read
(plus 3 per extra task in list_tasks)."""
import pytest
from delivery_api.store import PHASES
from delivery_api.tenancy import tenant_context


class CountingQueries:
    """Observe real native PG data queries, excluding the separate auth check.

    Transaction claim setup is outside _execute_native. No results are faked.
    """
    def __init__(self, store, monkeypatch):
        self.selects = 0
        self.auth_queries = 0
        self.statements = []
        execute = store._execute_native
        def counted(sql, parameters=(), *, write=False):
            self.statements.append(sql)
            if sql.lstrip().upper().startswith('SELECT'):
                if 'requi_session_active()' in sql:
                    self.auth_queries += 1
                else:
                    self.selects += 1
            return execute(sql, parameters, write=write)
        monkeypatch.setattr(store, '_execute_native', counted)


@pytest.fixture
def workspace(pg_workspace_factory, monkeypatch):
    from delivery_api import config, task_read_cache
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    task_read_cache.clear()
    with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
        yield workspace
    task_read_cache.clear()


@pytest.fixture
def make_store(workspace, monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    def make():
        store = PostgresStore(workspace.dsn.replace('user=sandbox', 'user=delivery_app'))
        return store, CountingQueries(store, monkeypatch)
    return make


@pytest.fixture
def http_client(workspace, pg_task_app):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    def build(store):
        return TestClient(pg_task_app(replace(workspace, store=store)), headers=workspace.headers)
    return build


def test_task_read_is_one_statement_with_the_full_record(make_store, workspace):
    store, connection = make_store()
    task = store.create_task('demo task')
    with workspace.admin() as db:
        for name, stale in [('intent.json', 0), ('old.json', 1)]:
            db.execute("INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,stale) VALUES (%s,1,'intent',%s,'ref',1,%s)",
                       (task['task_id'], name, stale))

    connection.selects = 0
    record = store.task(task['task_id'])

    assert connection.selects == 1
    assert [p['phase_key'] for p in record['phases']] == [key for key, _ in PHASES]
    assert [p['position'] for p in record['phases']] == list(range(len(PHASES)))
    assert all(p['state'] == 'pending' for p in record['phases'])
    assert [g['position'] for g in record['gates']] == list(range(len(PHASES)))
    assert all(isinstance(g['options'], list) for g in record['gates'])
    assert record['artifact_count'] == 1  # the stale one does not count


def test_list_tasks_is_one_statement_regardless_of_task_count(make_store):
    store, connection = make_store()
    for i in range(3):
        store.create_task('task %d' % i)

    connection.selects = 0
    tasks = store.list_tasks()

    assert connection.selects == 1
    assert len(tasks) == 3
    assert all(len(t['phases']) == len(PHASES) and len(t['gates']) == len(PHASES) for t in tasks)


def test_recorded_engine_session_reuses_the_request_read(monkeypatch):
    from delivery_api import app
    def boom(task_id):
        raise AssertionError('engine_session read twice in one request')
    monkeypatch.setattr(app.store, 'engine_session', boom)
    assert app._recorded_engine_session('t1', recorded='sess-1') == 'sess-1'


def test_recorded_engine_session_legacy_scan_reuses_request_events(monkeypatch):
    from delivery_api import app
    monkeypatch.setattr(app.store, 'engine_session', lambda task_id: None)
    monkeypatch.setattr(app.store, 'events', lambda task_id: (_ for _ in ()).throw(AssertionError('events read twice in one request')))
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', False)
    events = [{'message': 'interview started session: legacy-9', 'payload': {}}]
    assert app._recorded_engine_session('t1', recorded=None, events=events) == 'legacy-9'


def test_task_snapshot_is_one_statement_with_task_and_events(make_store):
    """The hot read (GET /api/tasks/<id>) needs the task record AND the newest
    events; they must arrive in ONE statement, not two."""
    store, connection = make_store()
    task = store.create_task('demo task')
    store.add_event(task['task_id'], 'progress', 'one')
    store.add_event(task['task_id'], 'progress', 'two')

    connection.selects = 0
    snapshot = store.task_snapshot(task['task_id'])

    assert connection.selects == 1
    assert snapshot['task']['task_id'] == task['task_id']
    assert [e['message'] for e in snapshot['events']] == ['one', 'two']  # ascending, like events()
    assert snapshot['events'][0]['payload'] == {}  # payload parsed like events()
    assert snapshot['task']['engine_session_id'] is None  # column rides the task row


def test_task_snapshot_missing_task_returns_none(make_store):
    store, connection = make_store()
    assert store.task_snapshot('nope') is None


def test_task_snapshot_event_limit_keeps_newest_ascending(make_store):
    store, connection = make_store()
    task = store.create_task('demo task')
    for i in range(5):
        store.add_event(task['task_id'], 'progress', 'm%d' % i)
    snapshot = store.task_snapshot(task['task_id'], event_limit=3)
    assert [e['message'] for e in snapshot['events']] == ['m2', 'm3', 'm4']


def test_get_task_is_one_statement_locally(make_store, http_client):
    """GET /api/tasks/<id> fan-out was task + engine_session + events (3
    statements). The snapshot read makes it ONE on a cold cache."""
    store, connection = make_store()
    task = store.create_task('demo task')
    client = http_client(store)

    connection.selects = 0
    reply = client.get('/api/tasks/%s' % task['task_id'])
    assert reply.status_code == 200
    assert connection.selects == 1, 'cold read must be the single snapshot statement'
    assert connection.auth_queries == 1, 'signed HTTP also validates the live auth session'


def test_get_task_second_read_serves_from_cache_zero_statements(monkeypatch, make_store, http_client):
    store, connection = make_store()
    task = store.create_task('demo task')
    client = http_client(store)
    assert client.get('/api/tasks/%s' % task['task_id']).status_code == 200

    connection.selects = 0
    connection.auth_queries = 0
    reply = client.get('/api/tasks/%s' % task['task_id'])
    assert reply.status_code == 200
    assert connection.auth_queries == 0, 'production session-check cache also serves its warm read'
    assert connection.selects == 0, 'warm read must not run task data queries'


def test_get_task_cache_invalidates_on_write_and_after_safety_ttl(monkeypatch, make_store, http_client):
    from delivery_api import task_read_cache
    store, connection = make_store()
    task = store.create_task('demo task')
    client = http_client(store)
    client.get('/api/tasks/%s' % task['task_id'])

    store.add_event(task['task_id'], 'progress', 'wrote through this process')
    connection.selects = 0
    client.get('/api/tasks/%s' % task['task_id'])
    assert connection.selects == 1, 'a write through this process must invalidate'

    client.get('/api/tasks/%s' % task['task_id'])  # warm again
    monkeypatch.setattr(task_read_cache, 'SAFETY_TTL_SECONDS', 0)  # force expiry
    connection.selects = 0
    client.get('/api/tasks/%s' % task['task_id'])
    assert connection.selects == 1, 'the 60s cross-process safety must force a re-read'


def test_retain_decision_review_repeated_is_a_noop_without_a_write(make_store):
    """While a decision waits, every poll pinned the same evidence again
    (INSERT + SELECT). Already-pinned evidence must cost one read, no write."""
    store, connection = make_store()
    task = store.create_task('demo task')
    connection.statements.clear()
    payload = {'wait_id': 'w1', 'revision': 'r1', 'options': [1, 2]}
    store.retain_decision_review(task['task_id'], 'w1', 'r1', payload)
    assert sum(not sql.lstrip().upper().startswith('SELECT') for sql in connection.statements) == 1
    store.retain_decision_review(task['task_id'], 'w1', 'r1', payload)
    assert sum(not sql.lstrip().upper().startswith('SELECT') for sql in connection.statements) == 1, 're-pinning identical evidence must not write again'


def test_retain_decision_review_conflict_still_raises(make_store):
    store, _ = make_store()
    task = store.create_task('demo task')
    store.retain_decision_review(task['task_id'], 'w1', 'r1', {'options': [1]})
    import pytest as _pytest
    with _pytest.raises(ValueError):
        store.retain_decision_review(task['task_id'], 'w1', 'r1', {'options': [2]})


def test_cached_read_cannot_cross_store_or_identity(monkeypatch, make_store):
    from delivery_api import task_read_cache, config
    from delivery_api.tenancy import TenantScope, tenant_context
    store, _ = make_store()
    other, _ = make_store()
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    a=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001')
    b=TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002')
    with tenant_context(a):
        task_read_cache.put(store,'same',{'task':{'title':'private'},'events':[]},read_version=task_read_cache.version(store,'same'))
        assert task_read_cache.get(other,'same') is None
        assert task_read_cache.get(store,'same')['task']['title']=='private'
    with tenant_context(b):
        assert task_read_cache.get(store,'same') is None


def test_local_commit_without_event_invalidates_cached_task(monkeypatch, make_store, http_client):
    store, connection = make_store()
    task=store.create_task('before')
    client=http_client(store)
    client.get('/api/tasks/'+task['task_id'])
    store.rename_task(task['task_id'],'after')
    connection.selects=0
    reply=client.get('/api/tasks/'+task['task_id'])
    assert reply.json()['title']=='after'
    assert connection.selects==1


def test_internal_task_read_bypasses_warm_cache(monkeypatch, make_store, http_client):
    from delivery_api import app
    store, connection=make_store()
    task=store.create_task('before')
    client=http_client(store)
    client.get('/api/tasks/'+task['task_id'])
    connection.selects=0
    assert app.get_task(task['task_id'])['title']=='before'
    assert connection.selects==1


def test_cache_rejects_mid_read_write_and_returns_copies(monkeypatch, make_store):
    from delivery_api import task_read_cache
    store,_=make_store()
    task=store.create_task('cache copies')
    tid=task['task_id']
    before=task_read_cache.version(store,tid)
    data=store.task_snapshot(tid)
    store.add_event(tid,'progress','mid-read change')
    task_read_cache.put(store,tid,data,read_version=before)
    assert task_read_cache.get(store,tid) is None
    task_read_cache.put(store,tid,data,read_version=task_read_cache.version(store,tid))
    first=task_read_cache.get(store,tid)
    first['task']['title']='not durable';first['events'].append({'message':'injected'})
    second=task_read_cache.get(store,tid)
    assert second['task']['title']=='cache copies' and second['events']==[]


def test_cache_expires_at_sixty_seconds_with_no_local_event(monkeypatch, make_store):
    from delivery_api import task_read_cache
    store,_=make_store()
    now=[100.0]
    monkeypatch.setattr(task_read_cache.time,'monotonic',lambda:now[0])
    task_read_cache.put(store,'t',{'task':{},'events':[]},read_version=task_read_cache.version(store,'t'))
    now[0]=159.99
    assert task_read_cache.get(store,'t') is not None
    now[0]=160.0
    assert task_read_cache.get(store,'t') is None
