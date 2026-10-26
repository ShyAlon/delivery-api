import os
import uuid
import pytest
from delivery_api.postgres_scope import ScopedPostgres
from delivery_api.tenancy import TenantScope, tenant_context, TenantAccessDenied

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
A=TenantScope('00000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-00000000000a')
B=TenantScope('00000000-0000-0000-0000-00000000000b','00000000-0000-0000-0000-00000000000b')


@pytest.fixture
def probe():
    import psycopg
    table='pool_probe_'+uuid.uuid4().hex[:10]
    with psycopg.connect(DSN,autocommit=True) as admin:
        admin.execute(f'CREATE TABLE {table}(owner text, note text)')
        admin.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        admin.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        admin.execute(f"CREATE POLICY own ON {table} USING (owner=current_setting('request.jwt.claim.sub',true)) WITH CHECK (owner=current_setting('request.jwt.claim.sub',true))")
        admin.execute(f'GRANT SELECT,INSERT ON {table} TO requi_backend')
    store=ScopedPostgres(DSN.replace('user=sandbox','user=delivery_app'))
    yield store,table
    store.close()
    with psycopg.connect(DSN,autocommit=True) as admin:admin.execute(f'DROP TABLE {table}')


def read(store,scope,table):
    with tenant_context(scope),store.transaction() as c:return [r['note'] for r in c.execute(f'SELECT note FROM {table}')]


def test_connections_are_reused_and_identity_never_carries_over(probe):
    store,table=probe
    with tenant_context(A),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'a-note')",(A.owner_id,))
    assert read(store,B,table)==[]                 # B reuses A's connection and sees nothing of A
    assert read(store,A,table)==['a-note']
    assert store.opened==1                          # one connection served all three transactions
    with store._idle[-1][0].transaction():
        c=store._idle[-1][0]
        assert c.execute("SELECT current_setting('request.jwt.claim.sub',true) AS s").fetchone()['s'] in (None,'')
        assert c.execute('SELECT current_user AS u').fetchone()['u']=='delivery_app'   # the role reset with the transaction


def test_failed_transaction_rolls_back_and_connection_stays_usable(probe):
    store,table=probe
    with pytest.raises(RuntimeError):
        with tenant_context(A),store.transaction() as c:
            c.execute(f"INSERT INTO {table} VALUES (%s,'lost')",(A.owner_id,));raise RuntimeError('boom')
    assert read(store,A,table)==[]
    assert store.opened==1


def test_connection_closed_by_the_server_is_replaced_before_work_runs(probe):
    import psycopg
    store,table=probe
    assert read(store,A,table)==[]
    pid=store._idle[-1][0].info.backend_pid
    with psycopg.connect(DSN,autocommit=True) as admin:admin.execute('SELECT pg_terminate_backend(%s)',(pid,))
    with tenant_context(A),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'after')",(A.owner_id,))
    assert read(store,A,table)==['after']
    assert store.opened==2


def test_missing_scope_is_still_denied(probe):
    store,_=probe
    with pytest.raises(TenantAccessDenied):
        with store.transaction():pass


def test_a_burst_larger_than_the_cap_waits_instead_of_failing(probe):
    import threading
    from delivery_api import postgres_scope
    store,table=probe
    results=[]
    def work(i):
        try:
            with tenant_context(A),store.transaction() as c:
                c.execute('SELECT pg_sleep(0.05)');results.append(c.info.backend_pid)
        except Exception as exc:results.append(exc)
    threads=[threading.Thread(target=work,args=(i,)) for i in range(40)]
    [t.start() for t in threads];[t.join(30) for t in threads]
    assert len(results)==40 and all(isinstance(r,int) for r in results)
    assert store.opened<=postgres_scope.POOL_OPEN_MAX and len(set(results))<=postgres_scope.POOL_OPEN_MAX


def test_one_request_shares_one_backend_and_the_next_request_sees_only_its_own_rows(probe):
    store,table=probe
    with tenant_context(A),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'a-note')",(A.owner_id,))
    with tenant_context(B),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'b-note')",(B.owner_id,))
    handle,token=store.begin_request(A)
    try:
        with tenant_context(A):
            seen=[]
            for _ in range(3):
                with store.transaction() as c:
                    seen.append((c.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'],[r['note'] for r in c.execute(f'SELECT note FROM {table}')]))
    finally:
        store.end_context(token);store.finish_request(handle,False)
    assert len({pid for pid,_ in seen})==1 and all(notes==['a-note'] for _,notes in seen)   # one connection, A's rows only
    assert read(store,B,table)==['b-note']                                                  # the connection carries nothing of A afterwards


def test_every_idle_connection_killed_by_the_server_is_still_replaced(probe):
    """Regression: two dead reused connections in a row must not leave _begin without a transaction
    (the unpack of its implicit None is the TypeError seen in the live log)."""
    import psycopg,threading
    store,table=probe
    barrier=threading.Barrier(2)
    def hold():
        with tenant_context(A),store.transaction() as c:
            barrier.wait(10);c.execute('SELECT 1')
    threads=[threading.Thread(target=hold) for _ in range(2)]
    [t.start() for t in threads];[t.join(15) for t in threads]
    assert all(not t.is_alive() for t in threads)
    assert len(store._idle)==2
    pids=[c.info.backend_pid for c,_ in store._idle]
    with psycopg.connect(DSN,autocommit=True) as admin:
        for pid in pids:admin.execute('SELECT pg_terminate_backend(%s)',(pid,))
    before=store.opened
    with tenant_context(A),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'alive')",(A.owner_id,))
    assert store.opened==before+1                    # one direct fresh connect replaces the dead idle, no pool drain
    assert read(store,A,table)==['alive']            # exactly one row: no statement was replayed across the retry
    assert store.opened==before+1                    # the fresh connection serves the next transaction


def test_many_requests_recover_together_after_the_idle_pool_is_killed(probe):
    """Concurrent requests hitting server-killed idles all recover via bounded direct fresh replacements."""
    import psycopg,threading
    store,table=probe
    barrier=threading.Barrier(3)
    def hold():
        with tenant_context(A),store.transaction() as c:
            barrier.wait(10);c.execute('SELECT 1')
    threads=[threading.Thread(target=hold) for _ in range(3)]
    [t.start() for t in threads];[t.join(15) for t in threads]
    pids=[c.info.backend_pid for c,_ in store._idle]
    with psycopg.connect(DSN,autocommit=True) as admin:
        for pid in pids:admin.execute('SELECT pg_terminate_backend(%s)',(pid,))
    results=[]
    def work(i):
        try:
            with tenant_context(A),store.transaction() as c:results.append(c.execute('SELECT 1 AS one').fetchone()['one'])
        except Exception as exc:results.append(exc)
    threads=[threading.Thread(target=work,args=(i,)) for i in range(10)]
    [t.start() for t in threads];[t.join(30) for t in threads]
    assert results==[1]*10


class _Dead:
    closed=False
    class _Tx:
        def __enter__(self):
            import psycopg;raise psycopg.OperationalError('server closed the connection unexpectedly')
        def __exit__(self,*exc):return False
    def transaction(self):return self._Tx()
    def close(self):pass


def test_a_dead_reused_connection_is_replaced_by_one_fresh_connection(probe):
    """After one dead reused connection the next candidate is a direct fresh connect, not another idle."""
    import psycopg
    store,table=probe
    checkouts=[]
    real_checkout=store._checkout
    store._checkout=lambda:(checkouts.append(1) or (_Dead(),True))
    try:
        with tenant_context(A),store.transaction() as c:c.execute(f"INSERT INTO {table} VALUES (%s,'fresh')",(A.owner_id,))
    finally:store._checkout=real_checkout
    assert len(checkouts)==1                       # exactly one doomed idle was tried
    assert read(store,A,table)==['fresh']


def test_a_failed_fresh_replacement_raises_its_own_error(probe):
    """If the direct fresh replacement cannot connect, that failure surfaces - no retry storm, no None."""
    import psycopg
    store,_=probe
    checkouts=[]
    store._checkout=lambda:(checkouts.append(1) or (_Dead(),True))
    store._checkout_fresh=lambda:(_ for _ in ()).throw(psycopg.OperationalError('server refused the connection'))
    try:
        with pytest.raises(psycopg.OperationalError,match='refused'):
            with tenant_context(A),store.transaction():pass
    finally:pass
    assert len(checkouts)==1


def test_a_capped_pool_falls_back_once_and_then_fails_clearly(probe):
    """With no fresh slot free the normal checkout is the fallback; a second dead candidate ends in a clear
    bounded error instead of an unpack of None."""
    import psycopg
    store,_=probe
    checkouts=[]
    store._checkout=lambda:(checkouts.append(1) or (_Dead(),True))
    store._checkout_fresh=lambda:None
    try:
        with pytest.raises(psycopg.OperationalError,match='replacement failed'):
            with tenant_context(A),store.transaction():pass
    finally:pass
    assert len(checkouts)==2
