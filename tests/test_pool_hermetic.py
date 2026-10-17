import sys
import types
import pytest
from delivery_api import postgres_scope
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
    def transaction(self):return FakeTransaction(self)
    def execute(self,sql,params=()):
        if self.dead:raise self.world.OperationalError('server closed the connection')
        self.log.append((sql,params))
    def close(self):self.closed=True


@pytest.fixture
def world(monkeypatch):
    w=types.SimpleNamespace(made=[],OperationalError=type('OperationalError',(Exception,),{}),dead_next=0)
    def connect(dsn,**kwargs):
        assert kwargs['prepare_threshold'] is None
        c=FakeConnection(w);w.made.append(c);return c
    fake=types.ModuleType('psycopg');fake.connect=connect;fake.OperationalError=w.OperationalError
    fake.pq=types.SimpleNamespace(TransactionStatus=Status)
    rows=types.ModuleType('psycopg.rows');rows.dict_row=object();fake.rows=rows
    monkeypatch.setitem(sys.modules,'psycopg',fake);monkeypatch.setitem(sys.modules,'psycopg.rows',rows)
    return w


def pool():
    p=postgres_scope.ScopedPostgres.__new__(postgres_scope.ScopedPostgres);p.dsn='x';p._init_pool();return p


def test_connection_is_reused_and_each_transaction_sets_its_own_identity(world):
    p=pool()
    for owner in (U1,U2):
        with tenant_context(TenantScope(owner,owner)),p.transaction():pass
    assert len(world.made)==1
    sets=[e for e in world.made[0].log if isinstance(e,tuple)]
    assert [e[1] for e in sets]==[(U1,U1),(U2,U2)]   # identity set every time, never inherited
    assert 'requi_backend' in sets[0][0]


def test_error_rolls_back_and_the_connection_is_still_reused(world):
    p=pool()
    with pytest.raises(ValueError):
        with tenant_context(S),p.transaction():raise ValueError('x')
    with tenant_context(S),p.transaction():pass
    assert len(world.made)==1 and world.made[0].log.count('rollback')==1


def test_dead_reused_connection_is_replaced_before_work_and_a_dead_new_one_raises(world):
    p=pool()
    with tenant_context(S),p.transaction():pass
    world.made[0].dead=True
    with tenant_context(S),p.transaction():pass
    assert len(world.made)==2 and world.made[0].closed
    p2=pool();orig=world.OperationalError
    fresh_dead=lambda dsn,**k:(world.made.append(FakeConnection(world,dead=True)),world.made[-1])[1]
    sys.modules['psycopg'].connect=fresh_dead
    with pytest.raises(orig):
        with tenant_context(S),p2.transaction():pass


def test_idle_connections_are_bounded_and_expire(world,monkeypatch):
    monkeypatch.setattr(postgres_scope,'POOL_IDLE_MAX',2)
    p=pool()
    held=[p._checkout()[0] for _ in range(4)]
    for c in held:p._release(c)
    assert len(p._idle)==2 and sum(c.closed for c in held)==2 and p._open==2
    now=postgres_scope.time.monotonic()
    monkeypatch.setattr(postgres_scope.time,'monotonic',lambda:now+postgres_scope.POOL_IDLE_SECONDS+1)
    c,reused=p._checkout()
    assert not reused and all(h.closed for h in held) and p._open==1


def test_open_connections_are_capped_and_a_waiter_gets_the_returned_one(world,monkeypatch):
    import threading,time
    monkeypatch.setattr(postgres_scope,'POOL_OPEN_MAX',2)
    p=pool();first,_=p._checkout();p._checkout()
    got=[];waiter=threading.Thread(target=lambda:got.append(p._checkout()));waiter.start();time.sleep(0.1)
    assert not got and len(world.made)==2          # the third caller waits; no third connection is opened
    p._release(first);waiter.join(5)
    assert got and got[0][0] is first and got[0][1] is True and len(world.made)==2


def test_waiting_too_long_fails_with_a_clear_message_and_a_failed_connect_frees_its_place(world,monkeypatch):
    monkeypatch.setattr(postgres_scope,'POOL_OPEN_MAX',1);monkeypatch.setattr(postgres_scope,'POOL_WAIT_SECONDS',0.2)
    p=pool();held,_=p._checkout()
    with pytest.raises(TimeoutError,match='connection limit'):p._checkout()
    p._release(held)
    sys.modules['psycopg'].connect=lambda dsn,**k:(_ for _ in ()).throw(world.OperationalError('refused'))
    p._idle.clear();p._open=0
    with pytest.raises(world.OperationalError):p._checkout()
    assert p._open==0
