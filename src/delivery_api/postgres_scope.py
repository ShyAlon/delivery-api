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
POOL_OPEN_MAX=10        # total connections this service holds open (in use + idle); below the database pooler's own client limit
POOL_WAIT_SECONDS=30    # how long a request waits for a free connection before failing with a clear message
POOL_IDLE_MAX=8
POOL_IDLE_SECONDS=240


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
_KEEPALIVE=dict(keepalives=1,keepalives_idle=30,keepalives_interval=10,keepalives_count=3)


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
        self._idle=[]            # (connection, returned_at), newest last
        self._pool_cond=threading.Condition()
        self._open=0             # connections currently open, in use or idle
        self.opened=0            # connections opened since start (diagnostics)

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        self.opened+=1
        return psycopg.connect(self.dsn,row_factory=dict_row,options='-c client_encoding=UTF8',prepare_threshold=None,**_KEEPALIVE)

    def _checkout(self):
        """A recently used idle connection, or a new one while fewer than POOL_OPEN_MAX are open; otherwise wait for one to come back.
        The database pooler refuses clients past its own limit, so a burst of requests queues here instead of failing there.
        Returns (connection, reused)."""
        deadline=time.monotonic()+POOL_WAIT_SECONDS
        with self._pool_cond:
            while True:
                now=time.monotonic()
                while self._idle:
                    connection,at=self._idle.pop()
                    if now-at<=POOL_IDLE_SECONDS and not connection.closed:return connection,True
                    self._forget(connection)
                if self._open<POOL_OPEN_MAX:
                    self._open+=1;break
                left=deadline-now
                if left<=0:raise TimeoutError('the database connection limit (%d) stayed reached for %d s'%(POOL_OPEN_MAX,POOL_WAIT_SECONDS))
                self._pool_cond.wait(left)
        try:return self._connect(),False
        except BaseException:
            with self._pool_cond:self._open-=1;self._pool_cond.notify()
            raise

    def _forget(self,connection):
        """Closes a connection that was counted as open and frees its place. Call with or without the lock held."""
        try:connection.close()
        except Exception:pass
        with self._pool_cond:self._open-=1;self._pool_cond.notify()

    def _checkout_fresh(self):
        """A brand new connection that skips the idle pool: the direct replacement for a reused connection that
        just died. Returns None when the open cap is reached, so the caller falls back to the normal checkout."""
        with self._pool_cond:
            if self._open>=POOL_OPEN_MAX:return None
            self._open+=1
        try:return self._connect()
        except BaseException:
            with self._pool_cond:self._open-=1;self._pool_cond.notify()
            raise

    def _drop(self,connection):
        self._forget(connection)

    def _release(self,connection):
        import psycopg
        usable=not connection.closed and not getattr(connection,'broken',False) and connection.info.transaction_status==psycopg.pq.TransactionStatus.IDLE
        if usable:
            with self._pool_cond:
                if len(self._idle)<POOL_IDLE_MAX:
                    self._idle.append((connection,time.monotonic()));self._pool_cond.notify();return
        self._forget(connection)

    def close(self):
        with self._pool_cond:idle,self._idle=self._idle,[]
        for connection,_ in idle:self._forget(connection)

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
        for attempt in (0,1):
            started=time.monotonic()
            if attempt==0:connection,reused=self._checkout()
            else:
                fresh=self._checkout_fresh()
                if fresh is not None:connection,reused=fresh,False
                else:connection,reused=self._checkout()
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
            'a reused database connection was already broken and its replacement failed before any statement ran') from last_error

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
