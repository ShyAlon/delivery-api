"""ScopedPostgres over the shared process pool: contract tests, hermetic.

The physical pool is psycopg_pool (db_pool) since the 2026-10-26 redesign;
these tests drive ScopedPostgres over a FakePool double and pin the CONTRACT:
reuse with per-transaction identity, rollback hygiene, dead-reused replacement
without replaying work, capped waiting with a clear error. Pool internals
(idle ageing, worker scheduling) belong to psycopg_pool's own test suite.
"""
import sys
import threading
import time
import types

import pytest
from psycopg_pool import PoolTimeout

from delivery_api import db_pool, postgres_scope
from delivery_api.tenancy import TenantScope, tenant_context

U1='00000000-0000-0000-0000-000000000001';U2='00000000-0000-0000-0000-000000000002'
S=TenantScope(U1,U1)


class Status:IDLE='idle'


class FakeTransaction:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):return self
    def __exit__(self,kind,value,trace):
        self.connection.log.append('rollback' if kind else 'commit');return False


class FakeConnection:
    def __init__(self,world,dead=False):
        self.world,self.closed,self.dead,self.log=world,False,dead,[]
        self.info=types.SimpleNamespace(transaction_status=Status.IDLE)
    def pipeline(self):return __import__("contextlib").nullcontext()
    def transaction(self):return FakeTransaction(self)
    def execute(self,sql,params=()):
        if self.dead:raise self.world.OperationalError('server closed the connection')
        self.log.append((sql,params))
    def close(self):self.closed=True


class FakePool:
    """A pool-shaped double: bounded, blocking getconn/putconn over FakeConnections."""
    def __init__(self,world,max_size=10):
        self.world=world;self.idle=[];self.open=0;self.max_size=max_size
        self.cond=threading.Condition()
    def getconn(self,timeout=None):
        deadline=time.monotonic()+(timeout if timeout is not None else 30)
        with self.cond:
            while True:
                if self.idle:return self.idle.pop()
                if self.open<self.max_size:
                    self.open+=1
                    break
                left=deadline-time.monotonic()
                if left<=0:raise PoolTimeout('pool exhausted')
                self.cond.wait(left)
        c=FakeConnection(self.world)
        c._delivery_pool_new=True   # mirrors db_pool._configure's brand-new mark
        self.world.made.append(c)
        return c
    def putconn(self,connection):
        with self.cond:
            if connection.closed:self.open-=1
            else:self.idle.append(connection)
            self.cond.notify()
    def close(self):pass


@pytest.fixture
def world(monkeypatch):
    w=types.SimpleNamespace(made=[],OperationalError=type('OperationalError',(Exception,),{}))
    fake=types.ModuleType('psycopg');fake.OperationalError=w.OperationalError
    fake.Transaction=FakeTransaction
    fake.pq=types.SimpleNamespace(TransactionStatus=Status)
    rows=types.ModuleType('psycopg.rows');rows.dict_row=object();fake.rows=rows
    monkeypatch.setitem(sys.modules,'psycopg',fake);monkeypatch.setitem(sys.modules,'psycopg.rows',rows)
    return w


def pool(world,max_size=10):
    p=postgres_scope.ScopedPostgres.__new__(postgres_scope.ScopedPostgres)
    p.dsn='x';p._pool=FakePool(world,max_size=max_size);p.opened=0
    return p


def test_connection_is_reused_and_each_transaction_sets_its_own_identity(world):
    p=pool(world)
    for owner in (U1,U2):
        with tenant_context(TenantScope(owner,owner)),p.transaction():pass
    assert len(world.made)==1
    sets=[e for e in world.made[0].log if isinstance(e,tuple)]
    assert [e[1] for e in sets]==[(U1,U1),(U2,U2)]   # identity set every time, never inherited
    assert 'requi_backend' in sets[0][0]


def test_error_rolls_back_and_the_connection_is_still_reused(world):
    p=pool(world)
    with pytest.raises(ValueError):
        with tenant_context(S),p.transaction():raise ValueError('x')
    with tenant_context(S),p.transaction():pass
    assert len(world.made)==1 and world.made[0].log.count('rollback')==1


def test_dead_reused_connection_is_replaced_before_work_and_a_dead_new_one_raises(world):
    p=pool(world)
    with tenant_context(S),p.transaction():pass
    world.made[0].dead=True
    with tenant_context(S),p.transaction():pass
    assert len(world.made)==2 and world.made[0].closed
    # A FRESH connection that dies on the identity claim fails the request:
    # retrying after a fresh connect risks nothing, but the clear error beats a silent loop.
    p2=pool(world)
    world.fresh_dead=True
    orig=world.OperationalError
    real_init=FakeConnection.__init__
    def dead_init(self,w,dead=False):real_init(self,w,dead=True)
    FakeConnection.__init__=dead_init
    try:
        with pytest.raises(orig):
            with tenant_context(S),p2.transaction():pass
    finally:
        FakeConnection.__init__=real_init


def test_open_connections_are_capped_and_a_waiter_gets_the_returned_one(world):
    p=pool(world,max_size=2)
    first,_=p._checkout();p._checkout()
    got=[];waiter=threading.Thread(target=lambda:got.append(p._checkout()));waiter.start();time.sleep(0.1)
    assert not got and len(world.made)==2          # the third caller waits; no third connection is opened
    p._release(first);waiter.join(5)
    assert got and got[0][0] is first and got[0][1] is True and len(world.made)==2


def test_waiting_too_long_fails_with_a_clear_message(world,monkeypatch):
    monkeypatch.setattr(postgres_scope,'POOL_WAIT_SECONDS',0.2)
    p=pool(world,max_size=1)
    held,_=p._checkout()
    with pytest.raises(TimeoutError,match='connection limit'):p._checkout()
    p._release(held)
    c,reused=p._checkout()
    assert c is held and reused


def test_a_mid_transaction_connection_never_goes_back(world):
    p=pool(world)
    c,_=p._checkout()
    c.info.transaction_status='intrans'
    p._release(c)
    assert c.closed and p._pool.open==0 and not p._pool.idle
