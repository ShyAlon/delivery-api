"""The ONE database connection pool for the whole delivery process.

The 2026-10-26 EMAXCONNSESSION incident: every service pinned connections per
concurrent actor and a few live sessions exhausted the 15-client Supabase
pooler. The redesign (interview-engine #62/#63 + this change): one
psycopg_pool per process (per DSN), bounded at PROCESS_POOL_MAX (default9). Delivery
traffic (postgres_scope) and the in-process interview engine
(TenantRegistryRouter pool= injection) both borrow from it.

Sharing is safe because identity is never pinned to a connection: delivery
applies role + claims per transaction (postgres_scope._SETUP), the engine
applies its claims per transaction (pg_claims), and a borrowed connection is
rolled back / discarded if returned mid-transaction. The budget is ONE number
for the whole process instead of per-actor arithmetic.
"""
import threading
import os

from psycopg_pool import ConnectionPool

# One number: the entire process - delivery traffic AND the in-process engine -
# shares this cap, below the Supabase pooler's 15-client limit (session mode).
# Pinned by tests/test_db_pool.py; the engine pins its standalone side.
from .child_pool_budget import pool_budget
PROCESS_POOL_MAX = pool_budget(os.environ)[0]
POOL_WAIT_SECONDS = 30    # a request waits this long for a free connection before failing clearly
POOL_IDLE_SECONDS = 240   # idle connections are retired after this

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()


def _configure(connection):
    # Marks brand-new connections so ScopedPostgres can report opened vs reused
    # exactly: the first borrower sees and clears the flag.
    connection._delivery_pool_new = True


def pool_for(dsn: str) -> ConnectionPool:
    """The shared pool for this DSN, created on first use. Every borrower in the
    process - stores, operator reads, the hosted engine - gets the same object."""
    with _lock:
        pool = _pools.get(dsn)
        if pool is None:
            import psycopg
            import psycopg.rows
            pool = ConnectionPool(
                dsn, min_size=0, max_size=PROCESS_POOL_MAX, max_idle=POOL_IDLE_SECONDS,
                timeout=POOL_WAIT_SECONDS, open=False, name='delivery-process',
                configure=_configure,
                kwargs={'row_factory': psycopg.rows.dict_row, 'autocommit': False,
                        'options': '-c client_encoding=UTF8', 'prepare_threshold': None,
                        'keepalives': 1, 'keepalives_idle': 30, 'keepalives_interval': 10,
                        'keepalives_count': 3})
            pool.open()
            _pools[dsn] = pool
        return pool


def close_all() -> None:
    """Process shutdown: close every shared pool. Per-store close() must NOT
    close the shared pool - other borrowers (and the hosted engine) still use it."""
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        try:
            pool.close()
        except Exception:
            pass
