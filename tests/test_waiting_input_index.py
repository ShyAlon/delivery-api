import uuid
from concurrent.futures import ThreadPoolExecutor
from test_postgres_store import store,A,DSN
from delivery_api.tenancy import tenant_context
from delivery_api.postgres_store import PostgresStore

def test_repeated_observation_uses_exact_indexed_identity_not_history(store,monkeypatch):
    with tenant_context(A):
        tid=store.create_task('indexed-input-'+uuid.uuid4().hex)['task_id']
        for n in range(100):store.add_event(tid,'waiting_input','old',payload={'input_id':'old-'+str(n)})
        original=store._execute_native
        def execute(sql,*a,**kw):
            if "kind='waiting_input'" in sql:
                assert "payload::jsonb->>'input_id'" in sql and 'LIMIT 1' in sql
            return original(sql,*a,**kw)
        monkeypatch.setattr(store,'_execute_native',execute)
        first=store.ensure_waiting_input_event(tid,'new',phase_key='intent',payload={'input_id':'a"quote'})
        assert store.ensure_waiting_input_event(tid,'repeat',phase_key='intent',payload={'input_id':'a"quote'})==first
        assert len(store.events(tid,limit=200))==101
        store.delete_task(tid)

def test_parallel_observation_deduplicates_under_task_lock(store):
    with tenant_context(A):tid=store.create_task('parallel-input-'+uuid.uuid4().hex)['task_id']
    def write(_):
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return db.ensure_waiting_input_event(tid,'waiting',phase_key='intent',payload={'input_id':'same'})
    with ThreadPoolExecutor(4) as pool:ids=list(pool.map(write,range(8)))
    assert len(set(ids))==1
    with tenant_context(A):store.delete_task(tid)

def test_index_plan_is_bounded_with_large_history(store):
    import time,json,statistics,psycopg
    with tenant_context(A):tid=store.create_task('index-plan-'+uuid.uuid4().hex)['task_id']
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO events(task_id,kind,message,phase_key,payload,ts) SELECT %s,'waiting_input','old','intent',json_build_object('input_id','input-'||n)::text,0 FROM generate_series(1,10000) n",(tid,))
        c.execute('ANALYZE events')
        plan=c.execute("EXPLAIN (FORMAT JSON) SELECT id FROM events WHERE task_id=%s AND kind='waiting_input' AND payload::jsonb->>'input_id'=%s ORDER BY id LIMIT 1",(tid,'input-9999')).fetchone()[0]
        assert 'events_waiting_input_identity' in json.dumps(plan)
        old=[];new=[]
        for _ in range(7):
            start=time.perf_counter();rows=c.execute("SELECT id,payload FROM events WHERE task_id=%s AND kind='waiting_input'",(tid,)).fetchall()
            found=next(e[0] for e in rows if json.loads(e[1])['input_id']=='input-9999');old.append((time.perf_counter()-start)*1000)
            start=time.perf_counter();match=c.execute("SELECT id FROM events WHERE task_id=%s AND kind='waiting_input' AND payload::jsonb->>'input_id'=%s ORDER BY id LIMIT 1",(tid,'input-9999')).fetchone()[0];new.append((time.perf_counter()-start)*1000)
        assert found==match
        print('\nWaiting input +10k local median old fetch/parse vs indexed lookup ms:',round(statistics.median(old),3),round(statistics.median(new),3))
    with tenant_context(A):store.delete_task(tid)
