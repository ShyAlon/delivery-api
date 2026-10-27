"""One read request pays for its database transaction once: a page load made ~9 transactions per call, each with its own round trips."""
import sys
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from delivery_api import postgres_scope
from delivery_api.tenant_http import TenantMiddleware, _shares_one_transaction
from delivery_api.tenancy import TenantScope, tenant_context

U1='00000000-0000-0000-0000-000000000001';U2='00000000-0000-0000-0000-000000000002'
S1=TenantScope(U1,U1);S2=TenantScope(U2,U2)


class Status:IDLE='idle'


class FakeTransaction:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):self.connection.log.append('begin');return self
    def __exit__(self,kind,value,trace):
        self.connection.log.append('rollback' if kind else 'commit');return False


class FakeConnection:
    def __init__(self,world):
        self.world,self.closed,self.log=world,False,[]
        self.info=types.SimpleNamespace(transaction_status=Status.IDLE)
    def pipeline(self):return __import__("contextlib").nullcontext()
    def transaction(self):return FakeTransaction(self)
    def execute(self,sql,params=()):
        if sql.startswith('ROLLBACK TO') and self.world.savepoint_missing:raise self.world.OperationalError('no such savepoint')
        self.log.append(sql.split(' ')[0] if not params else (sql,params))
    def close(self):self.closed=True


@pytest.fixture
def world(monkeypatch):
    w=types.SimpleNamespace(made=[],OperationalError=type('OperationalError',(Exception,),{}),savepoint_missing=False)
    def connect(dsn,**kwargs):
        c=FakeConnection(w);w.made.append(c);return c
    fake=types.ModuleType('psycopg');fake.connect=connect;fake.OperationalError=w.OperationalError
    fake.Transaction=FakeTransaction
    fake.pq=types.SimpleNamespace(TransactionStatus=Status)
    rows=types.ModuleType('psycopg.rows');rows.dict_row=object();fake.rows=rows
    monkeypatch.setitem(sys.modules,'psycopg',fake);monkeypatch.setitem(sys.modules,'psycopg.rows',rows)
    return w


class FakePool:
    """Pool-shaped double: getconn/putconn over FakeConnections, like psycopg_pool."""
    def __init__(self,world):self.world=world;self.idle=[]
    def getconn(self,timeout=None):
        if self.idle:return self.idle.pop()
        c=FakeConnection(self.world);c._delivery_pool_new=True
        self.world.made.append(c)
        return c
    def putconn(self,connection):
        if not connection.closed:self.idle.append(connection)
    def close(self):pass


def pool(world):
    p=postgres_scope.ScopedPostgres.__new__(postgres_scope.ScopedPostgres)
    p.dsn='x';p._pool=FakePool(world);p.opened=0
    return p


def count(connection,what):return sum(1 for e in connection.log if e==what)


def test_calls_in_one_request_share_one_connection_one_identity_setup_and_one_commit(world):
    p=pool(world)
    with tenant_context(S1):
        handle,token=p.begin_request(S1)
        for _ in range(5):
            with p.transaction() as connection:connection.execute('SELECT 1')
        p.end_context(token);p.finish_request(handle,False)
    assert len(world.made)==1
    log=world.made[0].log
    assert count(world.made[0],'begin')==1 and count(world.made[0],'commit')==1 and 'rollback' not in log
    assert sum(1 for e in log if isinstance(e,tuple))==1          # the identity statement ran once
    assert len(p._pool.idle)==1                                    # the connection went back for reuse


def test_a_request_that_made_no_database_call_never_opens_a_connection(world):
    p=pool(world)
    handle,token=p.begin_request(S1);p.end_context(token);p.finish_request(handle,False)
    assert world.made==[]


def test_another_identity_never_uses_the_shared_connection(world):
    p=pool(world)
    handle,token=p.begin_request(S1)
    with tenant_context(S1),p.transaction():pass
    with tenant_context(S2),p.transaction():pass
    p.end_context(token);p.finish_request(handle,False)
    assert len(world.made)==2                                      # the second identity got its own transaction
    own=[c for c in world.made if any(isinstance(e,tuple) and e[1][0]==U2 for e in c.log)]
    assert len(own)==1 and not any(isinstance(e,tuple) and e[1][0]==U1 for e in own[0].log)


def test_repeatable_read_is_never_shared(world):
    p=pool(world)
    handle,token=p.begin_request(S1)
    with tenant_context(S1):
        with p.transaction():pass
        with p.transaction(repeatable_read=True):pass
    p.end_context(token);p.finish_request(handle,False)
    assert len(world.made)==2


def test_a_failed_call_rolls_back_to_its_savepoint_and_the_request_still_commits(world):
    p=pool(world)
    with tenant_context(S1):
        handle,token=p.begin_request(S1)
        with pytest.raises(ValueError):
            with p.transaction():raise ValueError('boom')
        with p.transaction():pass
        p.end_context(token);p.finish_request(handle,False)
    c=world.made[0]
    assert len(world.made)==1 and count(c,'ROLLBACK')==1 and count(c,'commit')==1


def test_when_the_savepoint_cannot_be_undone_the_request_is_rolled_back_and_dropped(world):
    p=pool(world);world.savepoint_missing=True
    with tenant_context(S1):
        handle,token=p.begin_request(S1)
        with pytest.raises(ValueError):
            with p.transaction():raise ValueError('boom')
        with p.transaction():pass                                  # later calls fall back to their own transaction
        p.end_context(token);p.finish_request(handle,False)
    first=world.made[0]
    assert len(world.made)==2 and 'rollback' in first.log and first.closed


def test_a_failed_request_is_rolled_back(world):
    p=pool(world)
    with tenant_context(S1):
        handle,token=p.begin_request(S1)
        with p.transaction():pass
        p.end_context(token);p.finish_request(handle,True)
    assert 'rollback' in world.made[0].log and 'commit' not in world.made[0].log


def test_request_statistics_count_one_transaction_for_the_whole_request(world):
    p=pool(world);stats=postgres_scope.RequestStats();token=postgres_scope.request_stats.set(stats)
    try:
        with tenant_context(S1):
            handle,t=p.begin_request(S1)
            for _ in range(3):
                with p.transaction():pass
            p.end_context(t);p.finish_request(handle,False)
    finally:postgres_scope.request_stats.reset(token)
    assert (stats.transactions,stats.opened,stats.reused)==(1,1,0)
    assert stats.header().startswith('db;dur=') and '1 transactions' in stats.header()


def test_through_the_middleware_the_session_check_and_the_handler_share_one_transaction(world):
    p=pool(world)
    class Verifier:
        def verify(self,token):return S1
    def session_check():
        with p.transaction() as connection:connection.execute('SELECT active')
        return True
    app=FastAPI()
    @app.get('/api/thing')
    def thing():
        for _ in range(4):
            with p.transaction() as connection:connection.execute('SELECT 1')
        return {'ok':True}
    @app.get('/api/broken')
    def broken():
        with p.transaction():pass
        raise RuntimeError('boom')
    app.add_middleware(TenantMiddleware,verifier=Verifier(),session_check=session_check,request_database=p)
    with TestClient(app,raise_server_exceptions=False) as client:
        assert client.get('/api/thing',headers={'Authorization':'Bearer a'}).status_code==200
        made=len(world.made);c=world.made[0]
        assert made==1 and count(c,'begin')==1 and count(c,'commit')==1
        assert client.get('/api/broken',headers={'Authorization':'Bearer a'}).status_code==500
        assert 'rollback' in world.made[0].log                    # the failed request's transaction was undone, not committed
        assert len(world.made)==1                                  # and its connection was reused, not leaked


def test_only_plain_reads_share_a_transaction():
    assert _shares_one_transaction('GET','/api/tasks/abc')
    assert not _shares_one_transaction('POST','/api/tasks/abc')
    assert not _shares_one_transaction('GET','/api/tasks/abc/stream')
    assert not _shares_one_transaction('GET','/api/tasks/abc/artifacts.zip')
    assert not _shares_one_transaction('GET','/api/tasks/abc/artifacts/p/n')
    assert not _shares_one_transaction('GET','/tasks/abc/view')


def test_begin_pipelines_identity_after_begin_and_syncs_before_use(world,monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def pipeline(self):
        self.log.append('pipeline.enter')
        try:yield
        finally:self.log.append('pipeline.exit')
    monkeypatch.setattr(FakeConnection,'pipeline',pipeline,raising=False)
    p=pool(world)
    with tenant_context(S1),p.transaction() as conn:
        conn.execute('SELECT private_data')
    log=world.made[0].log
    assert log[0]=='pipeline.enter'
    assert log.index('begin') < next(i for i,e in enumerate(log) if isinstance(e,tuple))
    assert log.index('pipeline.exit') < log.index('SELECT')


def test_pipeline_setup_failure_never_dispatches_private_read(world,monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def pipeline(self):
        yield
        raise ValueError('identity rejected at pipeline synchronization')
    monkeypatch.setattr(FakeConnection,'pipeline',pipeline)
    p=pool(world)
    with pytest.raises(ValueError),tenant_context(S1),p.transaction() as conn:
        conn.execute('SELECT private_data')
    assert not any(e=='SELECT' for c in world.made for e in c.log)
    assert world.made[0].closed
