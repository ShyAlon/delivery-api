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
    """What one request cost, split by where it went: database, engine calls, pool checkout
    waits, auth checks and the window a connection was pinned. Shown in the Server-Timing
    header and the Debug dialog so slowness is measured, not guessed."""
    __slots__=('transactions','queries','db_seconds','reused','opened','engine_calls','engine_seconds','checkout_seconds','auth_seconds','tx_held_seconds','statements')
    MAX_STATEMENTS=500   # one pathological request cannot grow memory without bound
    def __init__(self):
        self.transactions=self.queries=self.reused=self.opened=0;self.db_seconds=0.0
        self.engine_calls=0;self.engine_seconds=self.checkout_seconds=self.auth_seconds=self.tx_held_seconds=0.0
        self.statements=[]
    def top_statement(self):
        """The (label, ms) of the slowest statement this request ran, or (None, 0)."""
        if not self.statements:return None,0
        label,ms=max(self.statements,key=lambda row:row[1]);return label,ms
    def header(self):
        return ('db;dur=%d;desc="%d queries, %d transactions, %d reused connections, %d new"'
                ', engine;dur=%d;desc="%d engine calls", checkout;dur=%d, auth;dur=%d, held;dur=%d'
                %(int(self.db_seconds*1000),self.queries,self.transactions,self.reused,self.opened,
                  int(self.engine_seconds*1000),self.engine_calls,int(self.checkout_seconds*1000),
                  int(self.auth_seconds*1000),int(self.tx_held_seconds*1000)))

request_stats=contextvars.ContextVar('delivery_request_stats',default=None)
_request_tx=contextvars.ContextVar('delivery_request_tx',default=None)


def note_query(seconds):
    stats=request_stats.get()
    if stats is not None:stats.queries+=1;stats.db_seconds+=seconds

def note_engine(seconds):
    """One HTTP call to the interview engine, made inside this request. No request context: a no-op."""
    stats=request_stats.get()
    if stats is not None:stats.engine_calls+=1;stats.engine_seconds+=seconds

def note_checkout(seconds):
    """Time spent WAITING for a pooled database connection (pool saturated). The dominant term
    when overlapping requests pin connections; today it hides inside db_ms or goes uncounted."""
    stats=request_stats.get()
    if stats is not None:stats.checkout_seconds+=seconds

def note_auth(seconds):
    """Identity verification and session checks for this request (Supabase round trips)."""
    stats=request_stats.get()
    if stats is not None:stats.auth_seconds+=seconds

def statement_label(sql):
    """SQL shape only: redact literals, quoted identifiers and comments."""
    import re
    text=str(sql);out=[];i=0
    while i<len(text):
        if text.startswith('--',i):
            end=text.find('\n',i+2);i=len(text) if end<0 else end+1;out.append(' ')
        elif text.startswith('/*',i):
            depth=1;i+=2
            while i<len(text) and depth:
                if text.startswith('/*',i):depth+=1;i+=2
                elif text.startswith('*/',i):depth-=1;i+=2
                else:i+=1
            out.append(' ')
        elif text[i] in ("'",'"'):
            quote=text[i];i+=1
            while i<len(text):
                if text[i]=='\\':i+=2
                elif text[i]==quote:
                    i+=1
                    if i<len(text) and text[i]==quote:i+=1
                    else:break
                else:i+=1
            out.append('?')
        elif text[i]=='$' and (m:=re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$',text[i:])):
            tag=m.group();end=text.find(tag,i+len(tag));i=len(text) if end<0 else end+len(tag);out.append('?')
        elif text[i].isdigit() and (i==0 or not (text[i-1].isalnum() or text[i-1]=='_')):
            m=re.match(r'[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?',text[i:]);i+=len(m.group());out.append('?')
        else:out.append(text[i]);i+=1
    return ' '.join(''.join(out).split())[:64] or 'statement'

def note_statement(label,seconds):
    """One timed statement with its label. No request context: a no-op."""
    stats=request_stats.get()
    if stats is not None and len(stats.statements)<RequestStats.MAX_STATEMENTS:
        stats.statements.append((label,int(seconds*1000)))

def _publish_notifications(task_ids):
    from . import event_bus
    for task_id in task_ids:event_bus.notify(task_id)


class TimingConnection:
    """Wraps a raw driver connection so every statement lands in the request's labeled
    statement list. Attributes other than execute pass through to the driver connection."""
    def __init__(self,inner,notifications=None):
        self._inner=inner;self._notifications=notifications
    def notify_after_commit(self,task_id):
        if self._notifications is None:raise RuntimeError('transaction notification scope required')
        self._notifications.add(task_id)
    def execute(self,sql,params=()):
        started=time.monotonic()
        try:return self._inner.execute(sql,params)
        except Exception as error:
            from .diagnostic_evidence import query_failure
            query_failure(error)
            raise
        finally:note_statement(statement_label(sql),time.monotonic()-started)
    def __getattr__(self,name):return getattr(self._inner,name)


class _RequestTransaction:
    """One transaction shared by every store call of one read request, so the BEGIN / identity / COMMIT round trips are paid once instead
    of once per call. Opened lazily by the first call, committed (or rolled back) when the request ends."""
    def __init__(self,postgres,scope):
        self.postgres,self.scope=postgres,scope
        self.lock=threading.RLock()
        self.connection=self.transaction=None
        self.broken=self.done=False
        self.cache_invalidations=set()
        self.notifications=set()
        self.first_begin=None   # when the shared transaction actually opened; the pin window

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
                _publish_notifications(self.notifications)
        except BaseException:
            self.postgres._drop(connection);raise
        finally:
            from . import task_read_cache
            for store in self.cache_invalidations:task_read_cache.invalidate(store)
            stats=request_stats.get()
            if stats is not None:
                stats.db_seconds+=time.monotonic()-started
                if self.first_begin is not None:stats.tx_held_seconds+=time.monotonic()-self.first_begin


def release_read_before_engine():
    """End a presentation read's DB batch before an engine needs the same pool.

    Keeps preceding reads batched; future store calls use short transactions.
    POST/answer/accounting have no shared read handle and are unaffected.
    """
    shared=_request_tx.get()
    if shared is not None:shared.finish(False)


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
        started=time.monotonic()
        operation_token=db_pool.borrower_operation.set("delivery-scoped-transaction")
        try:connection=self._pool.getconn(timeout=POOL_WAIT_SECONDS)
        except PoolTimeout:
            note_checkout(time.monotonic()-started)
            raise TimeoutError('database checkout unavailable after %g s (pool cap %d); see db.pool.timeout diagnostics'%(POOL_WAIT_SECONDS,POOL_OPEN_MAX))
        finally:db_pool.borrower_operation.reset(operation_token)
        note_checkout(time.monotonic()-started)
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
            # Use the public Transaction object directly: Connection.transaction()
            # entered inside pipeline() keeps nested pipeline contexts alive until
            # COMMIT, accidentally deferring later write errors to another call.
            transaction=psycopg.Transaction(connection)
            try:
                # psycopg pipeline batches BEGIN + transaction-local identity
                # on ONE connection. Exit synchronizes before any caller may
                # fetch private data; failed setup never dispatches a read.
                batch_started=time.monotonic()
                with connection.pipeline():
                    transaction.__enter__()
                    if repeatable_read:connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    connection.execute(_SETUP,(scope.owner_id,scope.session_id or ''))
                # A pipelined execute only measures enqueue time. Label each
                # statement once; attach the synchronized batch wall time to
                # identity setup (includes BEGIN), not invented per-SQL times.
                if repeatable_read:note_statement('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ [batched]',0)
                note_statement(statement_label(_SETUP),time.monotonic()-batch_started)
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
                    if shared.connection is None:
                        shared.connection,shared.transaction=self._begin(scope,False)
                        shared.first_begin=time.monotonic()
                    connection=shared.connection
                else:connection=None
            except BaseException:
                shared.lock.release();raise
            if connection is not None:
                try:
                    before=set(shared.notifications)
                    try:yield TimingConnection(connection,shared.notifications)
                    except BaseException:
                        shared.notifications.intersection_update(before)
                        # Undo only the failed operation; if that cannot be done the request's transaction is discarded at its end
                        # and later calls in the request use their own transaction.
                        try:connection.execute('ROLLBACK TO SAVEPOINT operation_start')
                        except BaseException:shared.broken=True
                        raise
                finally:shared.lock.release()
                return
            shared.lock.release()
        connection,transaction=self._begin(scope,repeatable_read)
        notifications=set()
        try:
            yield TimingConnection(connection,notifications)
        except BaseException:
            try:transaction.__exit__(*sys.exc_info())
            except BaseException:self._drop(connection)
            else:self._release(connection)
            raise
        try:transaction.__exit__(None,None,None)
        except BaseException:
            self._drop(connection);raise
        self._release(connection)
        _publish_notifications(notifications)

    def begin_request(self,scope):
        """Start sharing one transaction among the store calls of the current read request. Returns (handle, token); call end_context(token) in
        the same task, then finish_request(handle, failed) anywhere (it blocks on the database)."""
        handle=_RequestTransaction(self,scope)
        return handle,_request_tx.set(handle)

    @staticmethod
    def end_context(token):_request_tx.reset(token)

    @staticmethod
    def finish_request(handle,failed):handle.finish(failed)
