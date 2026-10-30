"""PG-selected sampler never opens a durable local metrics file."""
import pytest,psycopg
from pg_harness import STATE
from delivery_api import run_metrics

@pytest.fixture
def metrics(monkeypatch,tmp_path):
    with psycopg.connect(STATE['dsn'],autocommit=True) as c:
        c.execute('TRUNCATE resource_run_samples,resource_run_summaries,resource_container_samples,resource_container_summaries')
    monkeypatch.setenv('DELIVERY_METRICS_BACKEND','postgres')
    monkeypatch.setenv('DELIVERY_METRICS_SOURCE','runtime-host')
    monkeypatch.setattr(run_metrics.config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_METRICS_DB',str(tmp_path/'forbidden.db'))
    monkeypatch.setattr(run_metrics.sqlite3,'connect',lambda *a,**k: (_ for _ in ()).throw(AssertionError('SQLite metrics forbidden')))
    run_metrics.reset()
    yield run_metrics
    run_metrics.reset()
    assert list(tmp_path.iterdir())==[]

def test_sampler_roundtrip_restart_and_rollup(metrics,monkeypatch):
    monkeypatch.setattr(metrics,'read_group',lambda pid:dict(rss_mb=100.,cpu_seconds=1.,procs=1))
    s=metrics.RunSampler('task','session','srs',100)
    assert s.sample(42,now=110)['rss_mb']==100
    metrics.reset()
    got=metrics.for_task('task')
    assert got['problem'] is None and len(got['samples'])==1
    assert got['samples'][0]['session_id']=='session'
    assert metrics.overview()['runs'][0]['task_id']=='task'
    assert metrics.rollup(now=500000,raw_days=3)==(1,0)
    assert metrics.for_task('task')['summaries'][0]['samples']==1
    assert metrics.for_task('task')['samples']==[]

def test_container_roundtrip_and_unconfigured_source_fails_loud(metrics,monkeypatch):
    from delivery_api import run_slots
    monkeypatch.setattr(run_slots,'host_capacity',lambda:dict(mem_total_mb=1000,mem_available_mb=400))
    monkeypatch.setattr(metrics,'_container_cpu_seconds',lambda:1.)
    assert metrics.sample_container(now=100)['mem_used_mb']==600
    assert metrics.overview()['container']['last']['mem_used_mb']==600
    monkeypatch.delenv('DELIVERY_METRICS_SOURCE')
    assert metrics.sample_container(now=110) is None
    assert 'source' in metrics.overview()['problem'].lower()

def test_unknown_backend_does_not_fall_back(metrics,monkeypatch):
    monkeypatch.setenv('DELIVERY_METRICS_BACKEND','postgrez')
    assert metrics.overview()['problem']

def test_db_failure_is_visible_without_sqlite_fallback(metrics,monkeypatch):
    from delivery_api.postgres_metrics import PostgresMetrics
    from contextlib import contextmanager
    @contextmanager
    def broken(self):
        raise ConnectionError('hermetic unavailable')
        yield
    monkeypatch.setattr(PostgresMetrics,'transaction',broken)
    monkeypatch.setattr(metrics,'read_group',lambda pid:dict(rss_mb=1.,cpu_seconds=1.,procs=1))
    assert metrics.RunSampler('t',None,None,100).sample(1,now=110) is None
    assert 'ConnectionError' in metrics.for_task('t')['problem']
    assert 'ConnectionError' in metrics.overview()['problem']
