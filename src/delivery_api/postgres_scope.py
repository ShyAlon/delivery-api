"""Non-bypass, transaction-local request identity for PostgreSQL.

The database credential must be a dedicated restricted application LOGIN that
can SET ROLE requi_backend, but cannot become an owner/service/superuser role.
A Supabase superuser connection string is deliberately rejected.
"""
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
        """Starts the scoped transaction. A reused connection the server has closed is replaced once, before any work runs."""
        import psycopg
        for attempt in (0,1):
            connection,reused=self._checkout()
            transaction=connection.transaction()
            try:
                transaction.__enter__()
                if repeatable_read:connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                connection.execute(_SETUP,(scope.owner_id,scope.session_id or ''))
                return connection,transaction
            except psycopg.OperationalError:
                self._drop(connection)
                if not reused:raise
            except BaseException:
                self._drop(connection);raise

    @contextmanager
    def transaction(self,*,repeatable_read=False):
        scope = current_scope()
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
