"""Reproduce engine-first stale socket with real owned PG, no business replay."""
import time
import pytest
import psycopg
from delivery_api import db_pool
from delivery_api.postgres_scope import ScopedPostgres
from interview_engine.pg_pool import borrow_claimed
from delivery_api.tenancy import tenant_context


@pytest.fixture
def isolated(pg_scoped_workspace,monkeypatch):
    w=pg_scoped_workspace
    monkeypatch.setattr(db_pool,'_pools',{})
    s=ScopedPostgres(w.dsn.replace('user=sandbox','user=delivery_app'))
    try:yield w,s
    finally:db_pool.close_all()


def kill(w,pid):
    with w.admin() as c:c.execute('SELECT pg_terminate_backend(%s)',(pid,))
    time.sleep(.02)


def test_engine_first_stale_socket_cannot_escape_as_fresh(isolated):
    w,s=isolated
    with s._pool.connection() as raw:pid=raw.info.backend_pid
    kill(w,pid)
    with s.transaction() as c:assert c.execute('SELECT 42 AS n').fetchone()['n']==42


def test_engine_borrower_recovers_stale_shared_socket(isolated):
    w,s=isolated
    claims=(w.scope.owner_id,w.scope.session_id)
    with borrow_claimed(s._pool,claims) as c:pid=c.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    kill(w,pid)
    with borrow_claimed(s._pool,claims) as c:assert c.execute('SELECT 42 AS n').fetchone()['n']==42


def test_body_failure_is_not_replayed(isolated):
    w,s=isolated;entered=[]
    with pytest.raises(psycopg.OperationalError):
        with s.transaction() as c:
            entered.append(1)
            kill(w,c.info.backend_pid)
            c.execute('SELECT 42')
    assert entered==[1]
    with s.transaction() as c:assert c.execute('SELECT 43 AS n').fetchone()['n']==43


def test_failed_checks_are_bounded_by_checkout_timeout(pg_scoped_workspace):
    from psycopg_pool import ConnectionPool,PoolTimeout
    calls=[]
    def failed(conn):
        calls.append(1);conn.close();raise psycopg.OperationalError('injected dead checkout')
    with ConnectionPool(pg_scoped_workspace.dsn,min_size=0,max_size=1,check=failed,timeout=.1) as pool:
        start=time.monotonic()
        with pytest.raises(PoolTimeout):pool.getconn()
        assert time.monotonic()-start<2
    assert calls


def test_healthy_checkout_overhead_is_explicit(pg_scoped_workspace):
    from psycopg_pool import ConnectionPool
    samples=[]
    for checked in (False,True):
        calls=[]
        def validation(c):
            calls.append(1);time.sleep(.01);ConnectionPool.check_connection(c)
        with ConnectionPool(pg_scoped_workspace.dsn,min_size=1,max_size=1,
                            check=validation if checked else None) as pool:
            pool.wait()
            start=time.monotonic()
            for _ in range(20):
                with pool.connection() as c:pass
            samples.append(dict(checked=checked,check_calls=len(calls),checkouts=20,
                                wall_ms=round((time.monotonic()-start)*1000)))
    print('HEALTHY_CHECKOUT_10MS',samples)
    assert samples[0]['check_calls']==0 and samples[1]['check_calls']==20
    assert samples[1]['wall_ms']>=200


def test_check_does_not_carry_tenant_claims(isolated):
    w,s=isolated
    with s.transaction() as c:assert c.execute("SELECT current_setting('request.jwt.claim.sub',true) AS sub").fetchone()['sub']==w.scope.owner_id
    # Public raw checkout validation must not create a transaction or retain claims.
    with s._pool.connection() as c:
        assert c.info.transaction_status==psycopg.pq.TransactionStatus.IDLE
        assert not c.execute("SELECT current_setting('request.jwt.claim.sub',true) AS sub").fetchone()['sub']
