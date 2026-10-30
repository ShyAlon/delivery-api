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


def test_operator_crosshost_metrics_keep_distinct_host_series_and_require_registry(repo):
    from delivery_api.postgres_metrics import PostgresMetrics
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import tenant_context,TenantAccessDenied
    from test_postgres_store import A,B
    other=PostgresMetrics(repo.dsn,source_id='other-host')
    for r,size in ((repo,10),(other,90)):
        r.append_run(ts=100,run_key='same-run',task_id='task',session_id='session',phase='needs',pid=1,procs=1,rss_mb=size,peak_rss_mb=size,cpu_seconds=1,cpu_pct=size)
        r.append_container(ts=100,mem_used_mb=size,mem_limit_mb=100,cpu_seconds=1,cpu_pct=size)
    with psycopg.connect(STATE['dsn']) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'metrics-v1',ARRAY['logs.read'],true)",(A.owner_id,))
    reader=OperatorReadStore(repo.dsn,OperatorPrincipal(A.owner_id,'metrics-v1',frozenset({'logs.read'})))
    owner=PostgresStore(repo.dsn)
    try:
        with tenant_context(A):
            view=reader.metrics(owner,limit=10)
            assert len(view['runs'])==2 and {r['max_rss_mb'] for r in view['runs']}=={10,90}
            assert len(view['containers'])==2 and {r['source_id'] for r in view['containers']}=={'hermetic-host','other-host'}
            task=reader.metrics(owner,task_id='task',limit=10)
            assert len(task['samples'])==2 and len({r['source_id'] for r in task['samples']})==2
        with tenant_context(B),pytest.raises(TenantAccessDenied):reader.metrics(owner)
        with psycopg.connect(STATE['dsn']) as c:c.execute('UPDATE operator_principals SET enabled=false')
        with tenant_context(A),pytest.raises(TenantAccessDenied):reader.metrics(owner)
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM operator_principals')


def test_crosshost_projection_preserves_timeline_and_per_host_day_stats(repo):
    from contextlib import contextmanager
    from delivery_api.postgres_metrics import read_operator
    repo.append_run(ts=100,run_key='timeline',task_id='t',session_id=None,phase='needs',pid=1,procs=1,rss_mb=20,peak_rss_mb=20,cpu_seconds=1,cpu_pct=10)
    repo.append_run(ts=110,run_key='timeline',task_id='t',session_id=None,phase='needs',pid=1,procs=1,rss_mb=40,peak_rss_mb=40,cpu_seconds=2,cpu_pct=30)
    repo.append_container(ts=100,mem_used_mb=20,mem_limit_mb=100,cpu_seconds=1,cpu_pct=10)
    repo.append_container(ts=110,mem_used_mb=40,mem_limit_mb=100,cpu_seconds=2,cpu_pct=30)
    class Reader:
        @contextmanager
        def _read(self,*a,**kw):
            with repo.transaction() as c:yield c,{'registry_version':'fixture-projection'}
    view=read_operator(Reader(),None)
    assert view['runs'][0]['timeline_mb']==[20,40]
    assert view['containers'][0]['day']['am']==30 and view['containers'][0]['day']['ac']==20
    from delivery_api.run_metrics import metrics_text
    text=metrics_text(view)
    assert 'hermetic-host' in text and 'memory average 30' in text and '20 40' in text


def test_invalid_numbers_and_direct_sql_cannot_poison_metric_series(repo):
    row=dict(ts=100,run_key='invalid',task_id='t',session_id=None,phase='needs',pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    for field,value in (('ts',float('nan')),('cpu_pct',float('inf')),('rss_mb',-1),('pid',True),('task_id','x'*241)):
        with pytest.raises(ValueError):repo.append_run(**(row|{field:value}))
    for field in ('ts','mem_used_mb','cpu_pct'):
        values=dict(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1);values[field]=float('nan')
        with pytest.raises(ValueError):repo.append_container(**values)
    with repo.transaction() as c,pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO resource_container_samples(source_id,ts,mem_used_mb) VALUES(%s,'NaN',1)",(repo.source_id,))


def test_metrics_actual_lock_timeout_does_not_lose_rows(repo):
    import time
    with repo.transaction():
        start=time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
        assert time.monotonic()-start<1
    with repo.transaction() as c:assert c.execute('SELECT count(*) n FROM resource_container_samples').fetchone()['n']==0


def test_crosshost_metric_read_bounds_timeline_and_names_truncation(repo):
    from contextlib import contextmanager
    from delivery_api.postgres_metrics import read_operator
    for n in range(15):
        repo.append_run(ts=100+n,run_key='timeline',task_id='t',session_id=None,phase='needs',pid=1,procs=1,rss_mb=n,peak_rss_mb=n,cpu_seconds=n,cpu_pct=n)
    repo.append_run(ts=120,run_key='second',task_id='t',session_id=None,phase='needs',pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    class Reader:
        @contextmanager
        def _read(self,*a,**kw):
            with repo.transaction() as c:yield c,{'registry_version':'projection'}
    view=read_operator(Reader(),None,limit=1)
    assert view['truncated'] and len(view['runs'])==1
    view=read_operator(Reader(),None,limit=10)
    timeline=next(r['timeline_mb'] for r in view['runs'] if r['run_key']=='timeline')
    assert len(timeline)==12 and timeline[0]==0 and timeline[-1]==14


def test_retired_host_raw_is_summarized_by_another_host_without_widening_writer(repo):
    from delivery_api.postgres_metrics import PostgresMetrics
    retired=PostgresMetrics(repo.dsn,source_id='retired-container')
    retired.append_run(ts=100,run_key='retired',task_id='t',session_id=None,phase=None,pid=1,procs=1,rss_mb=20,peak_rss_mb=20,cpu_seconds=2,cpu_pct=10)
    retired.append_container(ts=100,mem_used_mb=20,mem_limit_mb=100,cpu_seconds=2,cpu_pct=10)
    assert repo.maintain(cutoff=200,now=300)==(1,1)
    assert repo.maintain(cutoff=200,now=301)==(0,0)
    with psycopg.connect(STATE['dsn']) as c:
        assert c.execute("SELECT samples,avg_rss_mb FROM resource_run_summaries WHERE source_id='retired-container'").fetchone()==(1,20.)
        assert c.execute('SELECT count(*) FROM resource_run_samples').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM resource_container_samples').fetchone()[0]==0
    with repo.transaction() as c:
        assert c.execute('SELECT count(*) n FROM resource_run_summaries').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM resource_container_summaries').fetchone()['n']==0
    with retired.transaction() as c:
        assert c.execute('SELECT count(*) n FROM resource_run_summaries').fetchone()['n']==1


def test_maintenance_skips_locked_host_and_rejects_live_retention_cutoff(repo):
    repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
    with repo.transaction():assert repo.maintain(cutoff=200,now=300)==(0,0)
    assert repo.maintain(cutoff=200,now=300)==(0,1)
    import time
    with pytest.raises((ValueError,psycopg.errors.InvalidParameterValue)):
        repo.maintain(cutoff=time.time(),now=time.time())


def test_maintenance_execute_is_not_inherited_by_public_customer_or_service_roles(repo):
    with psycopg.connect(STATE['dsn']) as c:
        for role in ('anon','authenticated','service_role','requi_backend','requi_operator_read'):
            assert c.execute("SELECT has_function_privilege(%s,'public.requi_maintain_resource_metrics(double precision,double precision)','EXECUTE')",(role,)).fetchone()[0] is False


def test_real_registry_metrics_snapshot_survives_concurrent_append(repo):
    from contextlib import contextmanager
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A
    from delivery_api.postgres_metrics import read_operator,PostgresMetrics
    other=PostgresMetrics(repo.dsn,source_id='concurrent-host')
    repo.append_container(ts=100,mem_used_mb=20,mem_limit_mb=100,cpu_seconds=1,cpu_pct=10)
    with psycopg.connect(STATE['dsn']) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'snapshot-v1',ARRAY['logs.read'],true)",(A.owner_id,))
    real=OperatorReadStore(repo.dsn,OperatorPrincipal(A.owner_id,'snapshot-v1',frozenset({'logs.read'})))
    class Reader:
        @contextmanager
        def _read(self,*a,**kw):
            with real._read(*a,**kw) as (c,authority):
                assert c.execute('SHOW transaction_isolation').fetchone()['transaction_isolation']=='repeatable read'
                # Registry read already established this transaction's snapshot.
                other.append_container(ts=110,mem_used_mb=90,mem_limit_mb=100,cpu_seconds=1,cpu_pct=10)
                yield c,authority
    try:
        with tenant_context(A):
            view=read_operator(Reader(),PostgresStore(repo.dsn))
            assert len(view['containers'])==1 and view['containers'][0]['source_id']==repo.source_id
            assert len(real.metrics(PostgresStore(repo.dsn))['containers'])==2
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM operator_principals')


def test_maintenance_keeps_hot_and_ambiguous_runs_and_blocks_late_rows(repo):
    row=dict(ts=100,run_key='complete',task_id='t',session_id=None,phase=None,pid=1,procs=1,rss_mb=1,peak_rss_mb=1,cpu_seconds=1,cpu_pct=1)
    repo.append_run(**row)
    repo.append_run(**(row|{'run_key':'hot'}))
    repo.append_run(**(row|{'run_key':'hot','ts':250}))
    for task in ('a','b'):repo.append_run(**(row|{'run_key':'ambiguous','task_id':task}))
    assert repo.maintain(cutoff=200,now=300)==(1,0)
    with pytest.raises(ValueError,match='already rolled up'):repo.append_run(**row)
    with repo.transaction() as c:
        assert c.execute('SELECT count(*) n FROM resource_run_samples').fetchone()['n']==4
    repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
    assert repo.maintain(cutoff=200,now=300)==(0,1)
    with pytest.raises(ValueError,match='already rolled up'):repo.append_container(ts=100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
    with pytest.raises(ValueError):repo.append_container(ts=1e100,mem_used_mb=1,mem_limit_mb=2,cpu_seconds=1,cpu_pct=1)
