import uuid
from decimal import Decimal
import psycopg,pytest
from test_postgres_store import store
from test_retail_ledger import account
from test_attempt_ledger import ledger,A,DSN
from delivery_api.tenancy import tenant_context
from delivery_api.retail_pricing import RetailRates

def test_read_totals_never_load_contribution_history_and_match_frozen_rates(account,monkeypatch):
    s,tid,job=account
    with tenant_context(A):
        for n in range(30):
            rates=RetailRates('0.0000125','0.0000005',str(1+n%3)).snapshot()
            s.post_retail_contribution(key=uuid.uuid4().hex,job_id=job,phase=['intent','conops'][n%2],kind='tokens',input_tokens=n,output_tokens=1,rates=rates)
        old=s._execute_native
        def guard(sql,*a,**kw):
            if 'FROM retail_contributions' in sql:raise AssertionError('history read on hot path')
            return old(sql,*a,**kw)
        monkeypatch.setattr(s,'_execute_native',guard)
        result=s.task_retail_totals(tid);phases=s.task_phase_credits(tid);by_job=s.retail_phase_totals(job)
        assert result=={'price':0.005812,'price_usd':0.011875}
        assert abs(sum(r['credits'] for r in phases)-result['price'])<0.000002
        assert sum(r['posted_units'] for r in by_job.values())==5812500

def test_private_atomic_projection_replay_and_operator_delete(account):
    s,tid,job=account;key=uuid.uuid4().hex;rates=RetailRates('0.0000125','0.0000005','1').snapshot()
    with tenant_context(A):
        assert s.task_retail_totals(tid) is None
        for _ in range(2):s.post_retail_contribution(key=key,job_id=job,phase='intent',kind='tokens',input_tokens=2,rates=rates)
        assert s.task_retail_totals(tid)['price']==.000025
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM retail_contributions WHERE contribution_key=%s',(key,))
    with tenant_context(A):assert s.task_retail_totals(tid) is None

def test_projection_rollback_permissions_and_parallel_posts(account):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope
    s,tid,job=account;rates=RetailRates('0.0000125','0.0000005','1').snapshot()
    def post(n):
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return db.post_retail_contribution(key=job+':parallel:'+str(n),job_id=job,phase='intent',kind='tokens',input_tokens=2,rates=rates)
    with ThreadPoolExecutor(4) as pool:list(pool.map(post,range(12)))
    with tenant_context(A):
        assert s.task_retail_totals(tid)['price']==.0003
        with pytest.raises(Exception):s.post_retail_contribution(key='bad-'+job,job_id=job,phase=None,kind='tokens',input_tokens=2,rates=rates)
        assert s.task_retail_totals(tid)['price']==.0003
    with psycopg.connect(DSN) as c:
        for role in ('anon','authenticated','service_role','requi_backend'):
            assert not c.execute("SELECT has_table_privilege(%s,'retail_totals','UPDATE')",(role,)).fetchone()[0]
        row=c.execute('SELECT credits,usd,posted_units FROM retail_totals WHERE scope=\'task\' AND ref=%s',(tid,)).fetchone()
        assert row==(Decimal('.0003'),Decimal('.0003'),300000)

def test_task_reassignment_and_empty_rows_preserve_display_semantics(account):
    s,tid,job=account;rates=RetailRates('0.0000125','0.0000005','1').snapshot()
    with tenant_context(A):
        new=s.create_task('new-task-'+uuid.uuid4().hex)['task_id']
        key=uuid.uuid4().hex
        s.post_retail_contribution(key=key,job_id=job,phase='intent',kind='tokens',input_tokens=0,rates=rates)
        assert s.task_retail_totals(tid)=={'price':0.0,'price_usd':0.0}
    with psycopg.connect(DSN) as c:c.execute('UPDATE worker_jobs SET task_id=%s WHERE job_id=%s',(new,job))
    with tenant_context(A):
        assert s.task_retail_totals(tid) is None
        assert s.task_retail_totals(new)=={'price':0.0,'price_usd':0.0}
    # Restore fixture ownership before its existing cleanup.
    with psycopg.connect(DSN) as c:c.execute('UPDATE worker_jobs SET task_id=%s WHERE job_id=%s',(tid,job))

def test_large_history_projection_measurement(account):
    import time,statistics,json
    s,tid,job=account;rates=RetailRates('0.0000125','0.0000005','1').snapshot()
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO retail_contributions SELECT %s||'-'||n,%s,%s,'intent','tokens',1,0,0,%s::jsonb,12500,12500,1000000000,'test',0 FROM generate_series(1,10000) n",(job,A.owner_id,job,json.dumps(rates)))
        c.commit()
        old=[];new=[]
        for _ in range(7):
            start=time.perf_counter();rows=c.execute('SELECT r.* FROM retail_contributions r JOIN worker_jobs w USING(job_id) WHERE w.task_id=%s',(tid,)).fetchall()
            total=sum(r[9]/Decimal(1000000000) for r in rows);old.append((time.perf_counter()-start)*1000)
            start=time.perf_counter()
            with tenant_context(A):value=s.task_retail_totals(tid)
            new.append((time.perf_counter()-start)*1000)
        assert value['price']==float(total)==.125
        print('\nRetail +10k local PG old fetch+sum/new scoped projection median ms:',round(statistics.median(old),3),round(statistics.median(new),3))
