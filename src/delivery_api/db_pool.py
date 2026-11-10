"""The ONE database connection pool for the whole delivery process.

The 2026-10-26 EMAXCONNSESSION incident: every service pinned connections per
concurrent actor and a few live sessions exhausted the 15-client Supabase
pooler. The redesign (interview-engine #62/#63 + this change): one
psycopg_pool per process (per DSN), bounded at PROCESS_POOL_MAX (default10). Delivery
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
import time
import contextvars
from psycopg_pool import PoolTimeout,ConnectionPool

borrower_operation=contextvars.ContextVar("db_borrower_operation",default="engine-or-background")


class ObservedPool(ConnectionPool):
    """Public checkout hooks cover both engine and delivery without proxy sockets."""
    def __init__(self,*args,**kwargs):
        self._borrower_lock=threading.Lock();self._borrowers={}
        super().__init__(*args,**kwargs)
    def getconn(self,timeout=None):
        started=time.monotonic()
        try:conn=super().getconn(timeout=timeout)
        except PoolTimeout:
            self._timeout_evidence(time.monotonic()-started)
            raise
        with self._borrower_lock:self._borrowers[id(conn)]=(time.monotonic(),borrower_operation.get())
        return conn
    def putconn(self,conn):
        # Remove BEFORE handing to a waiting borrower: otherwise its new entry
        # can be erased by this borrower's finally block after handoff.
        with self._borrower_lock:self._borrowers.pop(id(conn),None)
        return super().putconn(conn)
    def _timeout_evidence(self,elapsed):
        # Diagnostic failures must not replace the original checkout exception.
        try:
            from .observability import event
            now=time.monotonic()
            with self._borrower_lock:rows=list(self._borrowers.values())
            rows=sorted(rows,key=lambda row:row[0])
            allowed=("pool_min","pool_max","pool_size","pool_available","requests_waiting",
                     "requests_num","requests_queued","requests_wait_ms","requests_errors",
                     "connections_num","connections_errors","connections_lost","connections_ms")
            stats=self.get_stats()
            event("db.pool.timeout","Database checkout timed out",elapsed_ms=int(elapsed*1000),
                  **{key:stats[key] for key in allowed if key in stats},borrowed=len(rows),
                  borrowers=[dict(operation=op,age_ms=int((now-began)*1000)) for began,op in rows[:10]])
        except Exception:pass


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


def _traced_check(connection):
    """psycopg_pool's own liveness check (one empty-statement round trip), counted in the statement trace."""
    from . import statement_trace
    statement_trace.record('pool_check','POOL CHECK (empty statement)')
    return ConnectionPool.check_connection(connection)


def pool_for(dsn: str) -> ConnectionPool:
    """The shared pool for this DSN, created on first use. Every borrower in the
    process - stores, operator reads, the hosted engine - gets the same object."""
    with _lock:
        pool = _pools.get(dsn)
        if pool is None:
            import psycopg
            import psycopg.rows
            pool = ObservedPool(
                dsn, min_size=0, max_size=PROCESS_POOL_MAX, max_idle=POOL_IDLE_SECONDS,
                timeout=POOL_WAIT_SECONDS, open=False, name='delivery-process',
                configure=_configure,
                # A pool-level check covers EVERY borrower, including the engine
                # before delivery has consumed its diagnostic "new" marker.
                # Only an empty protocol query runs here, before any caller SQL;
                # psycopg_pool discards failures within the checkout timeout.
                check=_traced_check,
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
