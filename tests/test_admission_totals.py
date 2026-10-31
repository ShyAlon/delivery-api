"""Running admission totals must equal the original history semantics."""
import time,uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import psycopg,pytest
from test_attempt_ledger import ledger,Q
from test_postgres_store import store,DSN,A,B
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
pytestmark=pytest.mark.skipif(not DSN,reason='disposable Postgres required')

@contextmanager
def tx(s):
    if s._connection.get() is not None:
        yield s._connection.get();return
    with s._postgres.transaction() as c:
        token=s._connection.set(c)
        try:yield c
        finally:s._connection.reset(token)

def totals(s):
    with tx(s):
        return s._execute_native('SELECT * FROM public.requi_admission_totals(%s)',(time.time(),)).fetchone()

def oracle():
    with psycopg.connect(DSN) as c:
        return c.execute('''SELECT count(*) FILTER(WHERE input_estimate_overrun),
        count(*) FILTER(WHERE state IN ('reserved','dispatched','uncertain')),
        count(*) FILTER(WHERE state='uncertain'),
        count(*) FILTER(WHERE created_at>%s-rate_window_seconds),
        COALESCE(sum(CASE WHEN state='settled' THEN expense_actual ELSE expense_reserved END) FILTER(WHERE state IN ('reserved','dispatched','uncertain','failure_waived') OR (state='settled' AND created_at>%s-period_seconds)),0),
        COALESCE(sum(CASE WHEN state='settled' THEN retail_actual ELSE retail_reserved END) FILTER(WHERE state IN ('reserved','dispatched','uncertain','failure_waived') OR (state='settled' AND created_at>%s-period_seconds)),0)
        FROM provider_attempts JOIN admission_limits USING(owner_id) WHERE owner_id=%s''',
        (time.time(),time.time(),time.time(),A.owner_id)).fetchone()

def check(s):
    assert tuple(totals(s).values())==oracle()

def test_incremental_transitions_policy_expiry_and_admin_repair(ledger):
    s,tid,job=ledger
    with tenant_context(A):
        check(s)
        for action in ('cancel','settle','uncertain','release'):
            a=s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,action)['attempt_id'];check(s)
            if action!='cancel':s.transition_provider_attempt(a,'start');check(s)
            usage={'input_tokens':2,'output_tokens':3} if action=='settle' else {'kind':'connect-failure','error_class':'ConnectError','bytes_sent':0,'response_seen':False}
            s.transition_provider_attempt(a,action,usage);check(s)
            if action=='uncertain':
                with psycopg.connect(DSN) as c:c.execute("UPDATE provider_attempts SET state='failure_waived' WHERE attempt_id=%s",(a,))
                check(s)
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE provider_attempts SET created_at=%s WHERE job_id=%s',(time.time()-4000,job))
        check(s) # Old held/waived remain, settled and rate contributions expire.
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE admission_limits SET period_seconds=10000,rate_window_seconds=10000 WHERE owner_id=%s',(A.owner_id,))
        check(s) # Policy widening must rebuild from retained history.
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE provider_attempts SET input_estimate_overrun=true WHERE attempt_id=%s',(a,))
        check(s)
        with pytest.raises(AdmissionDenied,match='actual input'):s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'blocked')

def test_totals_atomic_rollback_and_tenant_permissions(ledger):
    s,tid,job=ledger
    with tenant_context(A):
        before=totals(s)
        with pytest.raises(RuntimeError):
            with tx(s):
                s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'rollback');assert totals(s)['held']==before['held']+1
                raise RuntimeError('rollback')
        assert totals(s)==before
        with pytest.raises(Exception):
            with tx(s):s._execute_native('UPDATE admission_totals SET held=0')
    with tenant_context(B):
        with pytest.raises(Exception,match='configuration missing'):totals(s)
        with tx(s):assert not s._execute_native('SELECT * FROM admission_totals WHERE owner_id=%s',(A.owner_id,)).fetchall()

def test_expiration_is_incremental_and_exact_at_boundary(ledger):
    s,tid,job=ledger;now=time.time()
    with tenant_context(A):
        a=s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'boundary')['attempt_id']
        s.transition_provider_attempt(a,'start');s.transition_provider_attempt(a,'settle',{'input_tokens':2,'output_tokens':3})
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE provider_attempts SET created_at=%s WHERE attempt_id=%s',(now,a))
            c.execute('UPDATE admission_limits SET period_seconds=5,rate_window_seconds=5 WHERE owner_id=%s',(A.owner_id,))
        with tx(s):
            early=s._execute_native('SELECT * FROM public.requi_admission_totals(%s)',(now+4,)).fetchone()
            edge=s._execute_native('SELECT * FROM public.requi_admission_totals(%s)',(now+5,)).fetchone()
        assert early['used_expense']-edge['used_expense']==8
        assert early['recent']-edge['recent']==1
        assert edge==totals_at(s,now+6)

def totals_at(s,now):
    with tx(s):return s._execute_native('SELECT * FROM public.requi_admission_totals(%s)',(now,)).fetchone()

def test_large_history_incremental_read_and_rollback_replay(ledger):
    s,tid,job=ledger
    with psycopg.connect(DSN) as c:
        c.execute('''INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at)
          SELECT 'history-'||%s||'-'||n,%s,%s,'test','{}','test','cancelled',0,0,%s,%s FROM generate_series(1,5000) n''',
          (job,A.owner_id,job,time.time()-100000,time.time()-100000))
    with tenant_context(A):
        check(s)
        a=s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'large')['attempt_id']
        check(s)
        with pytest.raises(RuntimeError):
            with tx(s):
                s.transition_provider_attempt(a,'start');s.transition_provider_attempt(a,'settle',{'input_tokens':1,'output_tokens':1})
                assert totals(s)['used_expense']==3
                raise RuntimeError('abort settlement')
        assert s.provider_attempt(a)['state']=='reserved';check(s)
        s.transition_provider_attempt(a,'cancel');s.transition_provider_attempt(a,'cancel');check(s)
    # Structural guard: the steady-state reader only reads history on lazy rebuild,
    # and expiry uses owner/time indexes, not a Python or SQL lifetime aggregation.
    from pathlib import Path
    sql=(Path(__file__).parents[1]/'supabase/migrations/20261031163000_admission_totals.sql').read_text()
    assert 'admission_rate_expiry' in sql and 'admission_spend_expiry' in sql
    from delivery_api import attempt_ledger
    assert 'SELECT * FROM provider_attempts WHERE owner_id' not in Path(attempt_ledger.__file__).read_text()

def test_parallel_settlement_and_reservation_matches_oracle(ledger):
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=ledger
    with tenant_context(A):
        a=s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'race')['attempt_id'];s.transition_provider_attempt(a,'start')
    def action(settle):
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):
            if settle:return db.transition_provider_attempt(a,'settle',{'input_tokens':2,'output_tokens':3})['state']
            try:return db.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'parallel')['state']
            except AdmissionDenied:return 'denied'
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(action,[True,False,False,False]))
    assert results[0]=='settled'
    assert results.count('reserved') in (1,2)
    with tenant_context(A):check(s)

def test_no_missed_first_build_race_and_schema_grants(ledger):
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=ledger
    # Remove only the derived cache, simulating first post-migration admission.
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM admission_totals WHERE owner_id=%s',(A.owner_id,))
    def read(_):
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return totals(db)
    with ThreadPoolExecutor(4) as pool:rows=list(pool.map(read,range(4)))
    assert all(r==rows[0] for r in rows)
    with tenant_context(A):check(s)
    with psycopg.connect(DSN) as c:
        for role in ('anon','authenticated','service_role','requi_backend'):
            assert not c.execute("SELECT has_table_privilege(%s,'admission_total_entries','INSERT')",(role,)).fetchone()[0]
            assert not c.execute("SELECT has_function_privilege(%s,'requi_admission_project(provider_attempts)','EXECUTE')",(role,)).fetchone()[0]


def test_running_total_read_cost_does_not_grow_with_owner_history(ledger):
    import statistics
    s,tid,job=ledger
    def measure():
        with tenant_context(A):totals(s) # Exclude one-time rebuild.
        old=[];new=[]
        with psycopg.connect(DSN) as c:
            for _ in range(7):
                start=time.perf_counter();c.execute('SELECT * FROM provider_attempts WHERE owner_id=%s',(A.owner_id,)).fetchall();old.append((time.perf_counter()-start)*1000)
                start=time.perf_counter()
                with tenant_context(A):totals(s)
                new.append((time.perf_counter()-start)*1000)
        return tuple(round(statistics.median(x),3) for x in (old,new))
    small=measure()
    with psycopg.connect(DSN) as c:
        c.execute('''INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at)
          SELECT 'bench-'||%s||'-'||n,%s,%s,'test','{}','test','cancelled',0,0,0,0 FROM generate_series(1,10000) n''',(job,A.owner_id,job))
    large=measure()
    print('\nAdmission local PG median ms, 7 samples; old full SELECT/new complete scoped projection read:',small,'then +10k history:',large)
    with tenant_context(A):check(s)
    with psycopg.connect(DSN) as c:
        assert c.execute('SELECT count(*) FROM admission_total_entries WHERE owner_id=%s',(A.owner_id,)).fetchone()[0]>=10000

def test_projection_tracks_operator_waiver_deletion_and_failed_update(ledger):
    s,tid,job=ledger
    with tenant_context(A):a=s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'operator')['attempt_id']
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE provider_attempts SET state='failure_waived' WHERE attempt_id=%s",(a,))
    with tenant_context(A):check(s);assert totals(s)['used_expense']==30
    with pytest.raises(Exception):
        with psycopg.connect(DSN) as c:c.execute('UPDATE provider_attempts SET owner_id=%s WHERE attempt_id=%s',(B.owner_id,a))
    with tenant_context(A):check(s)
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM provider_attempts WHERE attempt_id=%s',(a,))
    with tenant_context(A):check(s);assert totals(s)['used_expense']==0

def test_repeated_operator_changes_match_full_history_oracle(ledger):
    import random
    s,tid,job=ledger;rng=random.Random(92);now=time.time();ids=[uuid.uuid4().hex for _ in range(30)]
    with psycopg.connect(DSN) as c:
        for a in ids:
            c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'test','{}','test','cancelled',30,70,%s,%s)",
                      (a,A.owner_id,job,now-rng.choice([0,120,2000]),now))
    with tenant_context(A):check(s)
    for _ in range(50):
        a=rng.choice(ids);state=rng.choice(['reserved','dispatched','uncertain','failure_waived','settled','cancelled'])
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE provider_attempts SET state=%s,expense_actual=%s,retail_actual=%s,input_estimate_overrun=%s WHERE attempt_id=%s',
                      (state,8 if state=='settled' else None,18 if state=='settled' else None,rng.choice([True,False]),a))
        with tenant_context(A):check(s)
