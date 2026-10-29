"""Real disposable PG only. Metrics foundation never uses a SQLite fallback."""
import os
import pytest
import psycopg
from pg_harness import STATE

@pytest.fixture
def repo():
    from delivery_api.postgres_metrics import PostgresMetrics
    assert STATE['status']=='running'
    with psycopg.connect(STATE['dsn'],autocommit=True) as c:
        c.execute('TRUNCATE resource_run_samples,resource_run_summaries,resource_container_samples,resource_container_summaries')
    return PostgresMetrics(STATE['dsn'].replace('user=sandbox','user=delivery_app'),source_id='hermetic-host')

def test_samples_roundtrip_and_rollup_is_idempotent(repo):
    repo.append_run(ts=100,run_key='run-a',task_id='t1',session_id='s1',phase='srs',pid=42,procs=2,rss_mb=10,peak_rss_mb=10,cpu_seconds=1,cpu_pct=20)
    repo.append_run(ts=101,run_key='run-a',task_id='t1',session_id='s1',phase='srs',pid=42,procs=2,rss_mb=30,peak_rss_mb=30,cpu_seconds=2,cpu_pct=80)
    assert repo.rollup(cutoff=200,now=300)==(1,0)
    assert repo.rollup(cutoff=200,now=301)==(0,0)
    with psycopg.connect(STATE['dsn']) as c:
        row=c.execute("SELECT samples,avg_rss_mb,max_rss_mb,avg_cpu_pct FROM resource_run_summaries WHERE source_id='hermetic-host' AND run_key='run-a'").fetchone()
    assert row==(2,20.,30.,50.)

def test_writer_role_is_not_customer_read_or_grant(repo):
    repo.append_container(ts=100,mem_used_mb=50,mem_limit_mb=100,cpu_seconds=1,cpu_pct=20)
    with psycopg.connect(STATE['dsn'].replace('user=sandbox','user=delivery_app')) as c:
        with c.transaction():
            c.execute('SET LOCAL ROLE requi_backend')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT * FROM resource_container_samples')
    with psycopg.connect(STATE['dsn'].replace('user=sandbox','user=delivery_app')) as c:
        with c.transaction():
            c.execute('SET LOCAL ROLE requi_metrics_writer')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT * FROM credit_accounts')

def test_host_sources_do_not_collide_and_rollback_is_atomic(repo):
    from delivery_api.postgres_metrics import PostgresMetrics
    other=PostgresMetrics(STATE['dsn'].replace('user=sandbox','user=delivery_app'),source_id='another-host')
    for r in (repo,other):
        r.append_run(ts=500,run_key='same',task_id='t',session_id=None,phase=None,pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    repo.append_container(ts=100,mem_used_mb=50,mem_limit_mb=100,cpu_seconds=1,cpu_pct=20)
    assert repo.rollup(cutoff=600,now=700)==(1,1)
    with psycopg.connect(STATE['dsn']) as c:
        assert c.execute("SELECT count(*) FROM resource_run_samples WHERE source_id='another-host' AND run_key='same'").fetchone()[0]==1
    with pytest.raises(ValueError):repo.rollup(cutoff=float('nan'),now=1)

def test_no_silent_sqlite_or_schema_creation(tmp_path):
    from delivery_api.postgres_metrics import PostgresMetrics
    for bad in ('sqlite:///x','',str(tmp_path/'metrics.db')):
        with pytest.raises(ValueError):PostgresMetrics(bad,source_id='host')
    with pytest.raises(ValueError):PostgresMetrics(STATE['dsn'],source_id='')
    assert list(tmp_path.iterdir())==[]


def test_ambiguous_identity_rollup_rolls_back_without_losing_raw(repo):
    for task in ('task-a','task-b'):
        repo.append_run(ts=100,run_key='bad',task_id=task,session_id=None,phase=None,pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    with pytest.raises(ValueError,match='ambiguous'):repo.rollup(cutoff=200,now=300)
    with psycopg.connect(STATE['dsn']) as c:
        assert c.execute('SELECT count(*) FROM resource_run_samples').fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM resource_run_summaries').fetchone()[0]==0


def test_late_sample_cannot_overwrite_permanent_summary(repo):
    row=dict(ts=100,run_key='done',task_id='t',session_id=None,phase=None,pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    repo.append_run(**row);assert repo.rollup(cutoff=200,now=300)==(1,0)
    with pytest.raises(ValueError,match='already rolled up'):repo.append_run(**row)
    repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
    assert repo.rollup(cutoff=200,now=300)==(0,1)
    with pytest.raises(ValueError,match='already rolled up'):repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)


def test_role_cannot_read_other_sources_or_leak_after_pool_reuse(repo):
    repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
    from delivery_api.postgres_metrics import PostgresMetrics
    other=PostgresMetrics(STATE['dsn'].replace('user=sandbox','user=delivery_app'),source_id='other')
    with other.transaction() as c:
        assert c.execute('SELECT count(*) n FROM resource_container_samples').fetchone()['n']==0
    with repo.transaction() as c:
        assert c.execute('SELECT count(*) n FROM resource_container_samples').fetchone()['n']==1
    with psycopg.connect(STATE['dsn'].replace('user=sandbox','user=delivery_app')) as c:
        c.execute('SET LOCAL ROLE requi_operator_read')
        assert c.execute('SELECT count(*) FROM resource_container_samples').fetchone()[0]==0
