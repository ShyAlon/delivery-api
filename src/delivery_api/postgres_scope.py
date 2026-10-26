"""Non-bypass, transaction-local request identity for PostgreSQL.

The database credential must be a dedicated restricted application LOGIN that
can SET ROLE requi_backend, but cannot become an owner/service/superuser role.
A Supabase superuser connection string is deliberately rejected.
"""
import contextvars
import sys
import threading
import time
from contextlib import contextmanager
from .tenancy import current_scope, TenantAccessDenied

# A connection costs TCP + TLS + authentication, about a second to a remote database, so finished connections are kept for reuse.
# Reuse is safe because identity is transaction-local: every transaction starts by setting the role and the caller's claims, and
# both reset when the transaction ends, so a pooled connection carries no identity between requests.
# The physical pool is the ONE process pool (db_pool) shared with the in-process engine; the caps live there.
from . import db_pool
POOL_OPEN_MAX=db_pool.PROCESS_POOL_MAX   # kept as the public name of the process cap
POOL_WAIT_SECONDS=db_pool.POOL_WAIT_SECONDS
POOL_IDLE_SECONDS=db_pool.POOL_IDLE_SECONDS


class RequestStats:
    """What one request cost the database: shown in the Server-Timing header and the Debug dialog so slowness is measured, not guessed."""
    __slots__=('transactions','queries','db_seconds','reused','opened')
    def __init__(self):
        self.transactions=self.queries=self.reused=self.opened=0;self.db_seconds=0.0
    def header(self):
        return 'db;dur=%d;desc="%d queries, %d transactions, %d reused connections, %d new"'%(int(self.db_seconds*1000),self.queries,self.transactions,self.reused,self.opened)

request_stats=contextvars.ContextVar('delivery_request_stats',default=None)
_request_tx=contextvars.ContextVar('delivery_request_tx',default=None)


def note_query(seconds):
    stats=request_stats.get()
    if stats is not None:stats.queries+=1;stats.db_seconds+=seconds


class _RequestTransaction:
    """One transaction shared by every store call of one read request, so the BEGIN / identity / COMMIT round trips are paid once instead
    of once per call. Opened lazily by the first call, committed (or rolled back) when the request ends."""
    def __init__(self,postgres,scope):
        self.postgres,self.scope=postgres,scope
        self.lock=threading.RLock()
        self.connection=self.transaction=None
        self.broken=self.done=False

    def finish(self,failed):
        with self.lock:
            self.done=True
            connection,transaction=self.connection,self.transaction
            self.connection=self.transaction=None
        if connection is None:return
        started=time.monotonic()
        try:
            if failed or self.broken:
                try:transaction.__exit__(RuntimeError,RuntimeError('request failed'),None)
                except BaseException:pass
                self.postgres._drop(connection) if self.broken else self.postgres._release(connection)
            else:
                transaction.__exit__(None,None,None)
                self.postgres._release(connection)
        except BaseException:
            self.postgres._drop(connection);raise
        finally:
            stats=request_stats.get()
            if stats is not None:stats.db_seconds+=time.monotonic()-started


_SETUP="SELECT set_config('role','requi_backend',true), set_config('request.jwt.claim.sub',%s,true), set_config('request.jwt.claim.session_id',%s,true)"


class ScopedPostgres:
    def __init__(self, dsn: str):
        import psycopg
        self.dsn = dsn
        self._init_pool()
        # Fail at startup, not after the first user's data is exposed.
        with psycopg.connect(dsn) as connection:
            row = connection.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            if not row or any(row):
                raise TenantAccessDenied('application database role must not bypass RLS')
            unsafe = connection.execute("""
              SELECT 1 FROM pg_roles r
              WHERE (r.rolsuper OR r.rolbypassrls) AND
                    pg_has_role(current_user,r.oid,'MEMBER') LIMIT 1
            """).fetchone()
            if unsafe:
                raise TenantAccessDenied('application role can assume a bypass role')
            connection.execute('SET LOCAL ROLE requi_backend')
            tables = connection.execute("""
              SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relkind='r' AND
                (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR
                 pg_has_role(session_user,c.relowner,'MEMBER'))
            """).fetchall()
            if tables:
                raise TenantAccessDenied('unprotected table or application schema ownership')

    def _init_pool(self):
        # The ONE process pool (db_pool), shared with the in-process engine.
        # Identity is per-transaction on both sides, so no connection ever
        # carries an owner between borrowers.
        self._pool=db_pool.pool_for(self.dsn)
        self.opened=0            # connections opened since start (diagnostics)

    def _checkout(self):
        """A pooled connection, waiting up to POOL_WAIT_SECONDS for a free one.
        The database pooler refuses clients past its own limit, so a burst of requests queues here instead of failing there.
        Returns (connection, reused)."""
        from psycopg_pool import PoolTimeout
        try:connection=self._pool.getconn(timeout=POOL_WAIT_SECONDS)
        except PoolTimeout:
            raise TimeoutError('the database connection limit (%d) stayed reached for %d s'%(POOL_OPEN_MAX,POOL_WAIT_SECONDS))
        new=getattr(connection,'_delivery_pool_new',False)
        if new:
            connection._delivery_pool_new=False
            self.opened+=1
        return connection,not new

    def _drop(self,connection):
        # A broken connection never goes back: closing it first makes the pool
        # discard it at checkin and open a replacement on demand.
        try:connection.close()
        except Exception:pass
        try:self._pool.putconn(connection)
        except ValueError:pass  # not a pool connection (test doubles); already closed

    def _release(self,connection):
        import psycopg
        usable=not connection.closed and not getattr(connection,'broken',False) and connection.info.transaction_status==psycopg.pq.TransactionStatus.IDLE
        if not usable:
            # Never hand a mid-transaction connection to the next borrower.
            try:connection.close()
            except Exception:pass
        try:self._pool.putconn(connection)
        except ValueError:pass  # not a pool connection (test doubles); already closed

    def close(self):
        # The pool is process-shared (db_pool owns it): a store-level close
        # must not take connections away from other borrowers or the engine.
        # Process shutdown calls db_pool.close_all().
        pass

    def _begin(self,scope,repeatable_read):
        """Starts the scoped transaction. A reused connection the server has closed is dropped and replaced directly
        with one fresh connection, before any work runs: the replacement skips the idle pool, so a burst of
        server-killed idles costs one doomed round trip plus one connect, never one round trip per dead idle.
        A fresh connection's failure raises as before; if no fresh slot is free the normal checkout is the fallback,
        and if that also dies a clear error is raised instead of returning without a transaction. Retrying here
        cannot replay work: the only statements attempted on a dying candidate are the transaction settings and the
        identity claim, so a caller's read or write never runs twice."""
        import psycopg
        stats=request_stats.get()
        last_error=None
        # A reused connection the server has killed is dropped and the next
        # checkout tried, draining dead idles until a live one (or a fresh
        # connect) is found. Only the transaction settings and the identity
        # claim run on a dying candidate, so no caller statement is replayed.
        for attempt in range(POOL_OPEN_MAX+1):
            started=time.monotonic()
            connection,reused=self._checkout()
            transaction=connection.transaction()
            try:
                transaction.__enter__()
                if repeatable_read:connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                connection.execute(_SETUP,(scope.owner_id,scope.session_id or ''))
                if stats is not None:
                    stats.transactions+=1;stats.queries+=1;stats.db_seconds+=time.monotonic()-started
                    if reused:stats.reused+=1
                    else:stats.opened+=1
                return connection,transaction
            except psycopg.OperationalError as error:
                self._drop(connection)
                if not reused:raise
                last_error=error
            except BaseException:
                self._drop(connection);raise
        raise psycopg.OperationalError(
            'reused database connections were already broken and their replacement failed before any statement ran') from last_error

    @contextmanager
    def transaction(self,*,repeatable_read=False):
        scope = current_scope()
        shared=_request_tx.get()
        if shared is not None and not repeatable_read and shared.postgres is self and shared.scope==scope:
            shared.lock.acquire()
            try:
                if not (shared.done or shared.broken):
                    if shared.connection is None:shared.connection,shared.transaction=self._begin(scope,False)
                    connection=shared.connection
                else:connection=None
            except BaseException:
                shared.lock.release();raise
            if connection is not None:
                try:
                    try:yield connection
                    except BaseException:
                        # Undo only the failed operation; if that cannot be done the request's transaction is discarded at its end
                        # and later calls in the request use their own transaction.
                        try:connection.execute('ROLLBACK TO SAVEPOINT operation_start')
                        except BaseException:shared.broken=True
                        raise
                finally:shared.lock.release()
                return
            shared.lock.release()
        connection,transaction=self._begin(scope,repeatable_read)
        try:
            yield connection
        except BaseException:
            try:transaction.__exit__(*sys.exc_info())
            except BaseException:self._drop(connection)
            else:self._release(connection)
            raise
        try:transaction.__exit__(None,None,None)
        except BaseException:
            self._drop(connection);raise
        self._release(connection)

    def begin_request(self,scope):
        """Start sharing one transaction among the store calls of the current read request. Returns (handle, token); call end_context(token) in
        the same task, then finish_request(handle, failed) anywhere (it blocks on the database)."""
        handle=_RequestTransaction(self,scope)
        return handle,_request_tx.set(handle)

    @staticmethod
    def end_context(token):_request_tx.reset(token)

    @staticmethod
    def finish_request(handle,failed):handle.finish(failed)
