"""The process connection pool: ONE pool per DSN, one budget number."""
import types

import pytest

from delivery_api import db_pool


@pytest.fixture(autouse=True)
def _clean_pools():
    yield
    db_pool.close_all()


def test_pool_for_memoizes_one_pool_per_dsn(monkeypatch):
    made = []
    class FakePool:
        def __init__(self, dsn, **kwargs):
            self.dsn, self.kwargs, self.closed = dsn, kwargs, False
            made.append(self)
        def open(self): pass
        def close(self): self.closed = True
    monkeypatch.setattr(db_pool, 'ConnectionPool', FakePool)
    a1 = db_pool.pool_for('postgresql://a')
    a2 = db_pool.pool_for('postgresql://a')
    b = db_pool.pool_for('postgresql://b')
    assert a1 is a2 and a1 is not b and len(made) == 2
    db_pool.close_all()
    assert a1.closed and b.closed


def test_process_budget_is_one_number_under_the_pooler_limit():
    # Supabase pooler (session mode) allows 15 clients. ONE pool serves the
    # whole process - delivery traffic and the in-process engine borrow from
    # the same cap; there is no per-actor arithmetic left to exceed it.
    assert db_pool.PROCESS_POOL_MAX <= 15 - 5, \
        'the one process pool must leave headroom under the 15-client pooler limit'


def test_pool_shape_matches_the_engine_contract():
    # The engine receives this pool via TenantRegistryRouter(pool=...) and only
    # relies on the psycopg_pool public interface (getconn/putconn/connection).
    from psycopg_pool import ConnectionPool
    for method in ('getconn', 'putconn', 'connection', 'close'):
        assert callable(getattr(ConnectionPool, method)), method
