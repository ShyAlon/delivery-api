"""Differential proof for private confirmed-expense projections."""
import time,uuid,random
import psycopg,pytest
from test_attempt_ledger import credit_account,A,B,DSN
from delivery_api.tenancy import tenant_context

def refresh(c,now):
    c.execute('SELECT public.requi_refresh_expense_totals(%s)',(now,))

def check(c,now):
    refresh(c,now)
    rows=c.execute('SELECT bucket,value FROM expense_totals').fetchall();actual=dict(rows)
    expected={}
    def add(k,n):expected[k]=expected.get(k,0)+n
    add('users',c.execute('SELECT count(*) FROM auth.users').fetchone()[0])
    for owner,session,state,expense,stamp,provider,scale in c.execute('SELECT a.owner_id::text,w.session_id,a.state,a.expense_actual,a.settled_at,a.quote->>\'provider\',a.quote->>\'expense_units_per_usd\' FROM provider_attempts a JOIN worker_jobs w USING(job_id)'):
        if state=='uncertain':add('unknown',1);add('unknown:'+owner,1)
        if state!='settled':continue
        add('session:'+owner+':'+session,expense or 0)
        if stamp is not None and now-86400<stamp<=now:
            add('platform',expense or 0)
        if provider!='deepseek':continue
        if stamp is None or stamp>now:add('bad:'+owner,1)
        if stamp is not None and now-86400<stamp<=now:
            add('own:'+owner,expense or 0);add('active:'+owner,1)
            if expense is not None and scale is not None:add('valid:'+owner+':'+scale,1)
    assert {k:v for k,v in actual.items() if v}=={k:v for k,v in expected.items() if v}

def test_transition_time_boundary_repair_user_and_rollback(credit_account):
    s,tid,job=credit_account;now=time.time();ids=[]
    with psycopg.connect(DSN) as c:
        check(c,now)
        for state,stamp,expense,scale in [('settled',None,3,'1'),('settled',now+5,4,'1'),('settled',now-86400,7,'1'),('settled',now,8,'2'),('settled',now,None,'1'),('uncertain',now,None,'1')]:
            a=uuid.uuid4().hex;ids.append(a)
            c.execute('''INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,created_at,updated_at,settled_at)
             VALUES(%s,%s,%s,'test',%s::jsonb,'test',%s,10,10,%s,0,%s,%s,%s)''',
             (a,A.owner_id,job,__import__('json').dumps({'provider':'deepseek','expense_units_per_usd':scale}),state,expense,now,now,stamp))
            check(c,now)
        check(c,now+5);check(c,now+86400);check(c,now-1)
        user=uuid.uuid4();c.execute('INSERT INTO auth.users(id) VALUES(%s)',(user,));check(c,now)
        c.execute('DELETE FROM public.users WHERE user_id=%s',(user,));c.execute('DELETE FROM auth.users WHERE id=%s',(user,));check(c,now)
        c.commit()
        with pytest.raises(RuntimeError):
            with c.transaction():
                c.execute("UPDATE provider_attempts SET state='cancelled' WHERE attempt_id=%s",(ids[-1],));check(c,now)
                raise RuntimeError('rollback')
        check(c,now)
        for a in ids:c.execute('DELETE FROM provider_attempts WHERE attempt_id=%s',(a,))
        check(c,now)

def test_private_projection_and_guard_return_semantics(credit_account):
    s,tid,job=credit_account
    with psycopg.connect(DSN) as c:
        refresh(c,time.time())
        for role in ('anon','authenticated','service_role','requi_backend'):
            assert not c.execute("SELECT has_table_privilege(%s,'expense_totals','UPDATE')",(role,)).fetchone()[0]
            assert not c.execute("SELECT has_function_privilege(%s,'requi_refresh_expense_totals(double precision)','EXECUTE')",(role,)).fetchone()[0]
    with tenant_context(B),s._postgres.transaction() as c:
        assert c.execute('SELECT requi_confirmed_expense_guard(%s,1)',(job,)).fetchone()['requi_confirmed_expense_guard']=='worker_unavailable'

def test_random_mutations_match_original_formula(credit_account):
    import json
    s,tid,job=credit_account;rng=random.Random(44);now=time.time()
    with tenant_context(A):
        other=s.create_task('repair-'+uuid.uuid4().hex)['task_id'];sid='repair-'+uuid.uuid4().hex;s.set_engine_session(other,sid)
    with psycopg.connect(DSN) as c:
        ids=[]
        for n in range(35):
            a=uuid.uuid4().hex;ids.append(a)
            c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'test','{}','test','cancelled',10,10,0,0)",(a,A.owner_id,job))
        for _ in range(100):
            state=rng.choice(['settled','uncertain','dispatched','cancelled'])
            c.execute('UPDATE provider_attempts SET state=%s,settled_at=%s,expense_actual=%s,quote=%s::jsonb WHERE attempt_id=%s',
                (state,rng.choice([None,now-86400,now+1,now-100]),rng.choice([None,0,8]),json.dumps({'provider':rng.choice(['fake','deepseek']),'expense_units_per_usd':rng.choice([None,1,2])}),rng.choice(ids)))
            check(c,now)
        c.execute('UPDATE worker_jobs SET session_id=%s WHERE job_id=%s',(sid,job));check(c,now)
        for a in ids:c.execute('DELETE FROM provider_attempts WHERE attempt_id=%s',(a,))
        check(c,now)

def test_parallel_settlement_guard_expiry_and_single_episode(credit_account):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=credit_account;now=time.time();ids=[uuid.uuid4().hex for _ in range(6)]
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO expense_guard_policy VALUES(true,'race-test',10,1000000,1,3)")
        for a in ids:
            c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'test','{}','test','dispatched',10,10,%s,%s)",(a,A.owner_id,job,now,now))
        refresh(c,now)
    def settle(a):
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE provider_attempts SET state='settled',expense_actual=3,settled_at=%s WHERE attempt_id=%s",(time.time(),a))
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return db.confirmed_expense_admission(job,1)
    try:
        with ThreadPoolExecutor(6) as pool:reasons=list(pool.map(settle,ids))
        with psycopg.connect(DSN) as c:
            check(c,time.time())
            assert c.execute("SELECT count(*) FROM expense_guard_episodes WHERE scope_key=%s",('session:'+A.owner_id+':'+c.execute('SELECT session_id FROM worker_jobs WHERE job_id=%s',(job,)).fetchone()[0],)).fetchone()[0]==1
            assert c.execute("SELECT count(*) FROM expense_alert_outbox WHERE payload->>'policy_version'='race-test'").fetchone()[0]==1
        assert 'session_expense_limit' in reasons
    finally:
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM expense_alert_outbox WHERE payload->>'policy_version'='race-test'")
            c.execute('DELETE FROM expense_guard_policy')

def test_large_history_read_cost_and_rebuild(credit_account):
    import statistics
    s,tid,job=credit_account;now=time.time()
    with psycopg.connect(DSN) as c:
        refresh(c,now)
        c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,created_at,updated_at,settled_at) SELECT 'trip-bench-'||%s||'-'||n,%s,%s,'test','{}','test','settled',0,0,1,0,0,0 FROM generate_series(1,10000) n",(job,A.owner_id,job))
        old=[];new=[]
        for _ in range(7):
            start=time.perf_counter();c.execute("SELECT COALESCE(sum(a.expense_actual),0) FROM provider_attempts a JOIN worker_jobs w USING(job_id) WHERE a.state='settled' AND w.owner_id=%s AND w.session_id=(SELECT session_id FROM worker_jobs WHERE job_id=%s)",(A.owner_id,job)).fetchone();old.append((time.perf_counter()-start)*1000)
            start=time.perf_counter();refresh(c,time.time());c.execute('SELECT value FROM expense_totals WHERE bucket=%s',('session:'+A.owner_id+':'+c.execute('SELECT session_id FROM worker_jobs WHERE job_id=%s',(job,)).fetchone()[0],)).fetchone();new.append((time.perf_counter()-start)*1000)
        print('\nTripwire local PG +10k history, old session sum/new refresh+lookup median ms:',round(statistics.median(old),3),round(statistics.median(new),3))
        check(c,now-1) # Backward clock rebuild must still equal authoritative rows.
