#!/usr/bin/env python3
"""Bounded 1/2/4-user control-plane baseline. Own disposable PG, no network/model.

Uses real tenant RLS, worker claims, shared pool, run slots, document append and
usage projections. Fake work is only a fixed sleep + canned usage bytes, not a
new provider implementation. NOT an end-to-end generator or production capacity
claim. Local sockets avoid production network/DB/provider latency.
"""
import argparse,asyncio,json,os,socket,sys,time,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FORBIDDEN=('DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL','PGHOST','PGSERVICE','PGSERVICEFILE')

def fence():
    if any(os.environ.get(k) for k in FORBIDDEN):raise ValueError('refuse inherited database configuration; baseline owns disposable PG')
    original=socket.socket.connect
    def connect(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise ValueError('capacity baseline refuses network connections')
        return original(self,address)
    socket.socket.connect=connect

def alerts(row):
    return {'queue':row['queue_ms_max']>1000,'pool':row['pool_wait_ms_max']>250,
            'lock':row['db_lock_waiters_max']>0,'host':row['host_memory_fraction_max']>0.85,
            'db':row['db_connections_max']>12,'errors':bool(row['errors'])}

async def cohort(users,store,scopes,admin,pool,*,rounds,delay_ms,slots):
    from delivery_api import config,run_slots
    from delivery_api.tenancy import tenant_context
    from delivery_api.generator_documents import GeneratorDocuments
    from delivery_api.pg_usage import usage_summary
    from delivery_api.postgres_scope import RequestStats,request_stats
    config.MAX_CONCURRENT_RUNS=slots;run_slots.reset()
    queue=[];latencies=[];waits=[];errors=[];samples=[]
    started=time.monotonic();stop=asyncio.Event()
    async def sample():
        while not stop.is_set():
            active,locks=admin.execute("SELECT count(*),count(*) FILTER(WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()
            host=run_slots.host_capacity();total=host.get('mem_total_mb');available=host.get('mem_available_mb')
            samples.append((active,locks,(total-available)/total if total and available is not None else None))
            await asyncio.sleep(.05)
    def operation(scope,task,job,iteration):
        stats=RequestStats();token=request_stats.set(stats);began=time.monotonic()
        try:
            with tenant_context(scope):
                cap=store.claim_worker(job)
                if cap is None:raise ValueError('claim unavailable')
                docs=GeneratorDocuments(store,cap,job)
                # No socket/provider/model SDK. Predictable workload only.
                time.sleep(delay_ms/1000)
                raw=json.dumps(dict(stage='srs',provider='fake',model='canned',prompt_tokens=4,completion_tokens=2)).encode()+b'\n'
                docs.append('spec/usage.jsonl',raw)
                assert usage_summary(store,task)['tokens']==[0,6*(iteration+1)]
                docs.append('spec/log.txt',b'capacity baseline\n');store.finish_worker(job)
        finally:
            waits.append(stats.checkout_seconds*1000);latencies.append((time.monotonic()-began)*1000);request_stats.reset(token)
    async def user(scope):
        from types import SimpleNamespace
        with tenant_context(scope):
            task=store.create_task('capacity-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(task,'capacity-'+uuid.uuid4().hex)
            for iteration in range(rounds):
                job=store.enqueue_worker(task,'phase:srs');queued=time.monotonic()
                async def work():
                    queue.append((time.monotonic()-queued)*1000)
                    await asyncio.to_thread(operation,scope,task,job,iteration)
                try:await run_slots.run_limited(store,SimpleNamespace(runner=SimpleNamespace(processes={})),task,work)
                except Exception as exc:errors.append(type(exc).__name__)
    monitor=asyncio.create_task(sample())
    try:await asyncio.gather(*(user(s) for s in scopes[:users]))
    finally:stop.set();await monitor
    def maximum(items):return round(max(items,default=0),3)
    row=dict(users=users,rounds=rounds,slots=slots,seconds=round(time.monotonic()-started,3),completed=len(latencies)-len(errors),errors=errors,
        queue_ms_max=maximum(queue),work_ms_max=maximum(latencies),pool_wait_ms_max=maximum(waits),
        db_connections_max=max((s[0] for s in samples),default=0),db_lock_waiters_max=max((s[1] for s in samples),default=0),
        host_memory_fraction_max=max((s[2] for s in samples if s[2] is not None),default=0),pool_stats=pool.get_stats())
    row['alerts']=alerts(row);return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--rounds',type=int,default=3);p.add_argument('--delay-ms',type=int,default=50);p.add_argument('--slots',type=int,default=2);a=p.parse_args()
    if not 1<=a.rounds<=20 or not 0<=a.delay_ms<=1000 or not 1<=a.slots<=4:raise ValueError('bounded rounds 1..20, delay 0..1000ms, slots 1..4 required')
    fence();sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'src')]
    import pg_harness
    pg_harness.start()
    if pg_harness.STATE['status']!='running':raise RuntimeError('owned disposable PG did not start')
    import psycopg
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope
    from delivery_api import db_pool
    dsn=pg_harness.STATE['dsn'];app_dsn=dsn.replace('user=sandbox','user=delivery_app');scopes=[]
    from psycopg.conninfo import conninfo_to_dict
    from urllib.parse import urlencode
    fields=conninfo_to_dict(app_dsn);database=fields.pop('dbname')
    os.environ.update(DELIVERY_DATABASE_URL='postgresql:///'+database+'?'+urlencode(fields),DELIVERY_SUPABASE_URL='https://capacity.invalid',DELIVERY_SUPABASE_ANON_KEY='local-fake-public-key')
    try:
        with psycopg.connect(dsn,autocommit=True) as admin:
            for _ in range(4):
                scope=TenantScope(str(uuid.uuid4()),str(uuid.uuid4()));scopes.append(scope)
                admin.execute('INSERT INTO auth.users(id) VALUES(%s)',(scope.owner_id,))
                admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(scope.session_id,scope.owner_id))
            store=PostgresStore(app_dsn);pool=db_pool.pool_for(app_dsn)
            rows=[asyncio.run(cohort(n,store,scopes,admin,pool,rounds=a.rounds,delay_ms=a.delay_ms,slots=a.slots)) for n in (1,2,4)]
        result={'basis':'local disposable control-plane workload, not full generation or production capacity','network':'denied','paid_calls':0,'fake_delay_ms':a.delay_ms,
            'alerts_are':'explicit baseline thresholds, not deployed alerts','thresholds':{'queue_ms':1000,'pool_wait_ms':250,'db_lock_waiters':0,'host_memory_fraction':.85,'db_connections':12},'cohorts':rows}
        Path(a.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
        return int(any(r['errors'] for r in rows))
    finally:db_pool.close_all()

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:raise SystemExit('BASELINE FAILED: '+type(exc).__name__+'; no connection values printed')
