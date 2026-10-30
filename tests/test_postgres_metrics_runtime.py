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

def test_container_roundtrip_and_explicit_empty_source_fails_loud(metrics,monkeypatch):
    from delivery_api import run_slots
    monkeypatch.setattr(run_slots,'host_capacity',lambda:dict(mem_total_mb=1000,mem_available_mb=400))
    monkeypatch.setattr(metrics,'_container_cpu_seconds',lambda:1.)
    assert metrics.sample_container(now=100)['mem_used_mb']==600
    assert metrics.overview()['container']['last']['mem_used_mb']==600
    monkeypatch.setenv('DELIVERY_METRICS_SOURCE','')
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


def test_pg_application_defaults_to_pg_metrics_with_host_identity(monkeypatch,tmp_path):
    from delivery_api import config
    monkeypatch.delenv('DELIVERY_METRICS_BACKEND',raising=False)
    monkeypatch.delenv('DELIVERY_METRICS_SOURCE',raising=False)
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setattr('socket.gethostname',lambda:'fixed-host')
    pg=run_metrics._pg()
    assert pg is not None and pg.source_id=='host:fixed-host'
    monkeypatch.setenv('DELIVERY_METRICS_BACKEND','sqlite')
    with pytest.raises(ValueError,match='PostgreSQL'):run_metrics._pg()
    monkeypatch.setattr(config,'DATABASE_URL','sqlite:///'+str(tmp_path/'reference.db'))
    assert run_metrics._pg() is None


def test_operator_metrics_routes_never_use_process_writer_reads(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import app,config,run_slots
    calls=[]
    class Reader:
        def metrics(self,store,**kwargs):
            calls.append(kwargs)
            return {'runs':[],'containers':[],'container':None,'path':'postgres:resource_metrics','raw_days':3,'interval_s':10,'problem':None}
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(app,'_operator_reader',lambda:Reader())
    monkeypatch.setattr(run_metrics,'overview',lambda:(_ for _ in ()).throw(AssertionError('writer read forbidden')))
    monkeypatch.setattr(run_metrics,'for_task',lambda *a:(_ for _ in ()).throw(AssertionError('writer read forbidden')))
    actor=SimpleNamespace(admin=True)
    assert app.admin_operations_run_metrics('task',actor)['path']=='postgres:resource_metrics'
    assert 'Capacity:' in app.admin_operations_capacity(actor)['text']
    assert calls==[{'task_id':'task','limit':2000},{'limit':8}]


def test_default_pg_sampler_never_materializes_sqlite_and_hides_driver_secrets(monkeypatch,tmp_path):
    monkeypatch.setattr(run_metrics.config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.delenv('DELIVERY_METRICS_BACKEND',raising=False)
    monkeypatch.setenv('DELIVERY_METRICS_SOURCE','default-no-file')
    monkeypatch.setenv('DELIVERY_METRICS_DB',str(tmp_path/'forbidden.db'))
    monkeypatch.setattr(run_metrics,'read_group',lambda pid:dict(rss_mb=1.,cpu_seconds=1.,procs=1))
    monkeypatch.setattr(run_metrics.sqlite3,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('file forbidden')))
    run_metrics.reset()
    assert run_metrics.RunSampler('default-task',None,'needs',100).sample(1,now=110)
    run_metrics._failed(ConnectionError('postgresql://user:private-password@host/db'))
    assert 'private-password' not in run_metrics.for_task('default-task')['problem']
    assert not list(tmp_path.iterdir())
    run_metrics.reset()


@pytest.mark.parametrize('route',('capacity','run_metrics'))
def test_registry_metrics_unavailable_is_safe_503_not_private_driver_detail(monkeypatch,route):
    from delivery_api import app,config
    from fastapi import HTTPException
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    class Broken:
        def metrics(self,*a,**kw):raise ConnectionError('postgresql://private-password@host/db')
    monkeypatch.setattr(app,'_operator_reader',lambda:Broken())
    with pytest.raises(HTTPException) as got:
        if route=='capacity':app.admin_operations_capacity(None)
        else:app.admin_operations_run_metrics('',None)
    assert got.value.status_code==503
    assert 'private-password' not in str(got.value.detail)
