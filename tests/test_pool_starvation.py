"""Owned-PG starvation repro and bounded diagnostics, no providers."""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from delivery_api.tenant_http import SessionCheckCache
from delivery_api.postgres_scope import ScopedPostgres
from delivery_api.tenancy import tenant_context


def test_one_inflight_check_per_verified_identity():
    entered=[];release=threading.Event();start=threading.Barrier(13)
    def check():entered.append(1);release.wait(2);return False
    cache=SessionCheckCache(check)
    def read():start.wait();return cache.active('owner','session')
    with ThreadPoolExecutor(12) as pool:
        jobs=[pool.submit(read) for _ in range(12)];start.wait();time.sleep(.05)
        before=len(entered);release.set();assert all(j.result() is False for j in jobs)
    print('SAME_SESSION_CHECKS',before)
    assert before==1


def test_engine_call_releases_shared_read_connection(pg_scoped_workspace):
    import httpx
    from psycopg_pool import ConnectionPool
    from delivery_api.engine_client import HttpEngineClient
    w=pg_scoped_workspace
    with ConnectionPool(w.dsn.replace('user=sandbox','user=delivery_app'),min_size=0,max_size=1,timeout=.1) as pool:
        s=ScopedPostgres.__new__(ScopedPostgres);s._pool=pool;s.opened=0
        handle,token=s.begin_request(w.scope)
        def engine(request):
            # Same pool, in-process engine needs a free checkout.
            with pool.connection() as c:assert c.execute('SELECT 42').fetchone()[0]==42
            return httpx.Response(200,json={'ok':True})
        client=HttpEngineClient(base_url='http://test',transport=httpx.MockTransport(engine))
        try:
            from delivery_api.postgres_scope import RequestStats,request_stats
            stats=RequestStats();stat_token=request_stats.set(stats)
            for _ in range(4):
                with s.transaction() as c:c.execute('SELECT 1')
            assert stats.transactions==1
            request_stats.reset(stat_token)
            assert pool.get_stats()['pool_available']==0
            client._send('GET','/health')
            assert pool.get_stats()['pool_available']==1
            # After engine, subsequent calls get short separate transactions.
            with s.transaction() as c:c.execute('SELECT 2')
        finally:s.end_context(token);s.finish_request(handle,False);client._http.close()


def test_pool_timeout_records_gauges_and_borrower_age(pg_scoped_workspace,monkeypatch):
    from delivery_api import db_pool
    from psycopg_pool import PoolTimeout
    w=pg_scoped_workspace;monkeypatch.setattr(db_pool,'_pools',{})
    reports=[]
    monkeypatch.setattr('delivery_api.observability.event',lambda kind,message,**fields:reports.append((kind,fields)))
    pool=db_pool.pool_for(w.dsn)
    try:
        held=[pool.getconn() for _ in range(db_pool.PROCESS_POOL_MAX)]
        with pytest.raises(PoolTimeout):pool.getconn(timeout=.02)
        evidence=[f for k,f in reports if k=='db.pool.timeout']
        assert len(evidence)==1
        assert evidence[0]['pool_available']==0
        assert evidence[0]['borrowed']==db_pool.PROCESS_POOL_MAX
        assert len(evidence[0]['borrowers'])<=10
        assert 'dsn' not in str(evidence[0]).lower()
    finally:
        for c in held:pool.putconn(c)
        db_pool.close_all()


def test_singleflight_shares_failure_but_does_not_cache_it():
    calls=[];release=threading.Event();start=threading.Barrier(7)
    def check():
        calls.append(1);release.wait(2)
        if len(calls)==1:raise RuntimeError('offline')
        return True
    cache=SessionCheckCache(check)
    def read():
        start.wait()
        with pytest.raises(RuntimeError):cache.active('owner','session')
    with ThreadPoolExecutor(6) as pool:
        jobs=[pool.submit(read) for _ in range(6)];start.wait();time.sleep(.03);release.set()
        [j.result() for j in jobs]
    assert len(calls)==1
    assert cache.active('owner','session') is True
    assert len(calls)==2


def test_different_identity_does_not_wait_on_other_flight():
    release=threading.Event();entered=threading.Event()
    def check():
        if threading.current_thread().name.startswith('leader'):
            entered.set();release.wait(2)
        return True
    cache=SessionCheckCache(check)
    leader=threading.Thread(target=cache.active,args=('A','s'),name='leader')
    leader.start();assert entered.wait(1)
    try:assert cache.active('B','s') is True
    finally:release.set();leader.join()


def test_exception_path_keeps_request_timings(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import app as module
    from delivery_api.postgres_scope import request_stats,RequestStats
    reports=[]
    monkeypatch.setattr(module,'activity',lambda kind,message,**fields:reports.append((kind,fields)))
    monkeypatch.setattr(module.auth,'csrf_guard',lambda request:None)
    request=SimpleNamespace(method='GET',url=SimpleNamespace(path='/api/thing'),
                            state=SimpleNamespace(ref='test',diagnostic_started=time.monotonic()))
    async def fail(req):raise TimeoutError('checkout')
    stats=RequestStats();stats.checkout_seconds=.03;stats.auth_seconds=.04
    token=request_stats.set(stats)
    try:
        with pytest.raises(TimeoutError):asyncio.run(module._request_log(request,fail))
    finally:request_stats.reset(token)
    row=next(f for k,f in reports if k=='http.request.failed')
    assert row['checkout_ms']==30 and row['auth_ms']==40
    assert row['error_class']=='TimeoutError' and row['duration_ms']>=0


def test_ten_readers_release_before_engine_needs_shared_pool(pg_scoped_workspace):
    import httpx
    from psycopg_pool import ConnectionPool
    from delivery_api.engine_client import HttpEngineClient
    w=pg_scoped_workspace;gate=threading.Barrier(10)
    with ConnectionPool(w.dsn.replace('user=sandbox','user=delivery_app'),min_size=0,max_size=10,timeout=.5) as pool:
        scoped=ScopedPostgres.__new__(ScopedPostgres);scoped._pool=pool;scoped.opened=0
        def engine(request):
            with pool.connection() as c:c.execute('SELECT 42')
            return httpx.Response(200,json={})
        def read():
            with tenant_context(w.scope):
                handle,token=scoped.begin_request(w.scope)
                client=HttpEngineClient('http://test',transport=httpx.MockTransport(engine))
                try:
                    for _ in range(4):
                        with scoped.transaction() as c:c.execute('SELECT 1')
                    gate.wait()
                    client._send('GET','/snapshot')
                finally:scoped.end_context(token);scoped.finish_request(handle,False);client._http.close()
        with ThreadPoolExecutor(10) as workers:list(workers.map(lambda _:read(),range(10)))
        print('TEN_READERS_AFTER',pool.get_stats())
        assert pool.get_stats()['pool_available']==10
