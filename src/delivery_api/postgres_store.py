"""Postgres control plane sharing the existing relational state machine.

The SQL bridge handles the small SQLite dialect surface in shared operations.
Every public operation gets one scoped Postgres transaction. Nested operations
reuse that transaction, and commit/rollback is owned only by its outer boundary.
Local authentication/token tables have no place in this adapter.
"""
from __future__ import annotations
from contextvars import ContextVar
from functools import wraps
import json
import re
import secrets
import time
import contextlib,sqlite3
from .store import SQLiteStore, PHASES
from vmodel_contracts import RunState
from .postgres_scope import ScopedPostgres, note_query, note_statement, statement_label
from .tenancy import current_scope, TenantAccessDenied


class _Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class _Cursor:
    def __init__(self, cursor, *, returning=False):
        self.cursor = cursor
        self.lastrowid = cursor.fetchone()['id'] if returning else None
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self): return [_Row(row) for row in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())


class _Connection:
    def __init__(self, state, owner): self.state,self.owner = state,owner

    def execute(self, sql, parameters=()):
        connection = self.state.get()
        if connection is None: raise TenantAccessDenied('scoped transaction required')
        # All input SQL is application code, never request-supplied SQL. No literal
        # question marks exist in this fixed query surface.
        sql = sql.replace('?', '%s')
        if sql.startswith('INSERT OR IGNORE '):
            sql = sql.replace('INSERT OR IGNORE ', 'INSERT ',1)+' ON CONFLICT DO NOTHING'
        sql = sql.replace('cursor=max(cursor,excluded.cursor)',
                          'cursor=GREATEST(progress_ingest.cursor,excluded.cursor)')
        returning = bool(re.match(r'INSERT INTO (events|audit_log|review_comments|operator_failure_diagnostics)\b',sql))
        if returning: sql += ' RETURNING id'
        if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|TRUNCATE|WITH)\b",sql,re.I):
            from . import task_read_cache
            from .postgres_scope import _request_tx
            task_read_cache.invalidate(self.owner)
            self.owner._cache_dirty.set(True)
            shared=_request_tx.get()
            if shared is not None:shared.cache_invalidations.add(self.owner)
        started=time.monotonic()
        try:
            return _Cursor(connection.execute(sql,parameters),returning=returning)
        except Exception as exc:
            import psycopg
            if isinstance(exc,psycopg.errors.UniqueViolation):
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise
        finally:
            elapsed=time.monotonic()-started
            note_query(elapsed)  # TimingConnection owns statement labels exactly once

    def executemany(self,sql,parameters):
        for row in parameters: self.execute(sql,row)

    def commit(self): pass  # outermost operation commits
    def rollback(self):
        # Failed compare-and-swap operations return False without durable writes.
        # Roll back to the outer savepoint so the connection remains usable.
        self.state.get().execute('ROLLBACK TO SAVEPOINT operation_start')


def _transactional(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        if self._connection.get() is not None:
            return method(self,*args,**kwargs)
        with contextlib.ExitStack() as cleanup:
            dirty_token=self._cache_dirty.set(False)
            def invalidate_after_transaction():
                dirty=self._cache_dirty.get()
                self._cache_dirty.reset(dirty_token)
                if dirty:
                    from . import task_read_cache
                    task_read_cache.invalidate(self)
            cleanup.callback(invalidate_after_transaction)
            connection=cleanup.enter_context(self._postgres.transaction())
            connection.execute('SAVEPOINT operation_start')
            token = self._connection.set(connection)
            try:
                action=_TASK_ENTRY_OPERATIONS.get(method.__name__)
                if action:
                    from .prepaid import owner_lock
                    from .suspension_policy import require_task_operation
                    owner_lock(self)
                    require_task_operation(self,action)
                return method(self,*args,**kwargs)
            finally: self._connection.reset(token)
    return call


def validator_provenance(module_path,model=None):
    """Provenance of the code that will actually validate: installed distribution
    version for the module (e.g. 'vmodel_contracts.srs' -> vmodel-contracts==0.28.0)
    plus, when the pydantic model is passed, two exactly-labelled fingerprints:
    'schema=' is a fingerprint of the model's JSON schema only - it does NOT capture
    custom validator Python behavior - and 'source=' is a hash of the validator
    module's source bytes ('source=unknown' when unreadable, e.g. a build without
    sources). Together they record which code ran; they do NOT fully distinguish two
    implementations that share version, schema and module source (e.g. logic in a
    helper module). An editable install keeps one version string across commits, which
    is why the fingerprints exist. 'unknown' is explicit; never a guess like 'latest'."""
    import hashlib,importlib.metadata,importlib.util,json
    top=module_path.split('.')[0]
    version='unknown'
    distributions=importlib.metadata.packages_distributions().get(top,[])
    if not distributions:
        # Some installers omit top_level.txt and their inference can miss valid
        # installed wheels. RECORD is authoritative ownership evidence: match
        # the imported module's exact resolved file, never guess a dist name.
        from pathlib import Path
        try:module_spec=importlib.util.find_spec(module_path)
        except (ModuleNotFoundError,ValueError):module_spec=None
        origin=Path(module_spec.origin).resolve() if module_spec and module_spec.origin else None
        if origin is not None:
            distributions=[d.metadata['Name'] for d in importlib.metadata.distributions()
                if any(str(f).startswith(top+'/') and Path(d.locate_file(f)).resolve()==origin for f in d.files or [])]
    for dist in distributions:
        try:version=f'{dist}=={importlib.metadata.version(dist)}';break
        except importlib.metadata.PackageNotFoundError:continue
    if model is None:return version
    schema=json.dumps(model.model_json_schema(),sort_keys=True).encode()
    parts=f'{version};schema={hashlib.sha256(schema).hexdigest()[:16]}'
    source='unknown'
    spec=importlib.util.find_spec(module_path)
    if spec and spec.origin and spec.origin.endswith('.py'):
        try:source=hashlib.sha256(open(spec.origin,'rb').read()).hexdigest()[:16]
        except OSError:pass
    return f'{parts};source={source}'


class PostgresStore(SQLiteStore):
    def __init__(self,dsn):
        self._postgres = ScopedPostgres(dsn)
        self._connection = ContextVar('postgres_operation',default=None)
        self._cache_dirty = ContextVar('postgres_cache_dirty',default=False)
        self._db = _Connection(self._connection,self)
        # No store-level mutex on Postgres. The SQLiteStore lock serializes a
        # single shared native connection; here every store call runs on the
        # calling thread's own scoped connection (ContextVar) inside a scoped
        # transaction, so PG row locks and READ COMMITTED provide the
        # serialization - exactly the semantics the multi-host topology
        # requires anyway. A process-wide mutex held across a blocking PG
        # statement inverts lock order against request-scoped transactions,
        # which hold their row locks for the whole request: one request holds
        # the mutex and waits on another's row lock while that request holds
        # the row lock and waits on the mutex - a deadlock PG cannot detect.
        # Inherited SQLiteStore methods still say `with self._lock:`; the
        # no-op keeps them unchanged. Cross-call atomicity never came from
        # this mutex (it is released between calls): read-modify-write paths
        # use SELECT ... FOR UPDATE, CAS upserts and the advisory owner lock.
        self._lock = contextlib.nullcontext()

    def ensure_waiting_input_event(self,task_id:str,message:str,*,phase_key:str,payload:dict)->int:
        # Lock before checking. Under READ COMMITTED, a waiter then sees the
        # winner's committed event. This is a database row lock, never a Python
        # mutex held across a request's transaction or another PG lock wait.
        if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
            raise KeyError(task_id)
        return self._ensure_waiting_input_locked(task_id,message,phase_key=phase_key,payload=payload)

    def _task_operation_guard(self,action):
        # Private scoped lease for synchronous file+DB task edits. Hold shared
        # owner lock until all writes finish, not only a stale preflight read.
        from contextlib import contextmanager
        @contextmanager
        def guard():
            from .prepaid import owner_lock
            from .suspension_policy import require_task_operation
            if self._connection.get() is not None:
                owner_lock(self);require_task_operation(self,action)
                yield
                return
            with self._postgres.transaction() as connection:
                connection.execute('SAVEPOINT operation_start')
                token=self._connection.set(connection)
                try:
                    owner_lock(self);require_task_operation(self,action)
                    yield
                finally:self._connection.reset(token)
        return guard()

    def auth_session_active(self):
        return bool(self._db.execute('SELECT public.requi_session_active() AS active').fetchone()['active'])

    def record_failure_diagnostics(self, task_id: str, source: str, http_status: int, block: dict) -> int:
        """Tenant-scoped operator-only failure evidence on Postgres: the row
        carries the caller's verified owner and the task's generation, and the
        write policy checks owner_id against the session claim."""
        block = self._validated_diagnostics_block(block)
        owner = current_scope().owner_id
        task = self.task(task_id)
        if not task:
            raise KeyError('task not found')
        row = self._db.execute(
            "INSERT INTO operator_failure_diagnostics(owner_id,task_id,generation,source,http_status,error_class,sqlstate,stage,role,"
            "sub_present,session_id_present,session_active_for_sub,message,block,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (owner, task_id, task['generation'], source, http_status, block['error_class'], block.get('sqlstate'),
             block.get('stage'), block['role'], str(block['sub_present']), str(block['session_id_present']),
             str(block['session_active_for_sub']), block['message'], json.dumps(block), time.time()))
        return row.lastrowid

    def create_task(self,title,brief='',*,task_id=None):
        title=title.strip()
        if not title: raise ValueError('task name cannot be empty')
        task_id,now=task_id or secrets.token_hex(6),time.time()
        owner=current_scope().owner_id
        try:
            self._db.execute('INSERT INTO tasks(task_id,title,brief,state,model_route,generation,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?)',
                             (task_id,title,brief,'not_started','deepseek',1,now,now,owner))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f'a task named {title!r} already exists') from exc
        self._db.executemany("INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (?,?,?,?,'pending')",
                             [(task_id,key,label,i) for i,(key,label) in enumerate(PHASES)])
        self._db.executemany("INSERT INTO gates(task_id,gate_key,after_phase,label,position,is_open,state,options) VALUES (?,?,?,?,?,0,'idle',?)",
            [(task_id,f'gate-{key}',key,f'{label} gate',i,json.dumps([f'Approve {label}','Choose another option']))
             for i,(key,label) in enumerate(PHASES)])
        self.audit(owner,'task.created',task_id,{'title':title})
        return self.task(task_id)

    def create_link(self,founder_label):
        token,run_id,now=secrets.token_urlsafe(24),secrets.token_hex(8),time.time()
        self._db.execute('INSERT INTO runs(run_id,state,budget_usd,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?)',
                         (run_id,RunState.CREATED.value,6.,now,now,current_scope().owner_id))
        self._db.execute('INSERT INTO links VALUES (?,?,?,1,?)',(token,run_id,founder_label,now))
        return {'token':token,'run_id':run_id}

    def add_usage(self,run_id,record_json,cost_usd):
        return self.add_user_usage(run_id,current_scope().owner_id,record_json,cost_usd,0,0)

    def attribute_run(self,run_id,user_id):
        if user_id != current_scope().owner_id: raise TenantAccessDenied('cannot change run owner')
        if not self.run(run_id): raise KeyError(run_id)

    def user_quota(self,user_id):
        if user_id != current_scope().owner_id: return None
        row=self._db.execute('SELECT user_id,label,token_quota FROM users WHERE user_id=?',(user_id,)).fetchone()
        if row is None: return None
        out=dict(row)
        out['user_id']=str(out['user_id'])
        out['tokens_used']=self._db.execute('SELECT COALESCE(SUM(input_tokens+output_tokens),0) n FROM usage WHERE user_id=?',(user_id,)).fetchone()['n']
        out['email']=None;out['is_admin']=0
        return out

    # Same one-statement projection as the sqlite store, in Postgres dialect:
    # jsonb aggregation cast to text so _task_record parses both identically.
    _TASK_PROJECTION = """
SELECT t.*,
  (SELECT coalesce(jsonb_agg(to_jsonb(r))::text,'[]') FROM (
     SELECT p.phase_key,p.label,p.position,p.state,p.detail,p.completed,p.total,p.gate_basis_changed,p.started_at,p.completed_at
     FROM phases p WHERE p.task_id=t.task_id ORDER BY p.position) r) AS phases_json,
  (SELECT coalesce(jsonb_agg(to_jsonb(r))::text,'[]') FROM (
     SELECT g.* FROM gates g WHERE g.task_id=t.task_id ORDER BY g.position) r) AS gates_json,
  (SELECT count(*) FROM task_artifacts a WHERE a.task_id=t.task_id AND a.stale=0) AS artifact_count
FROM tasks t"""

    # Dialect twin of SQLiteStore._EVENTS_SUBQUERY (jsonb_agg).
    _EVENTS_SUBQUERY = """
  (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.id DESC)::text,'[]') FROM (
     SELECT e.id,e.task_id,e.phase_key,e.kind,e.message,e.payload,e.ts
     FROM events e WHERE e.task_id=t.task_id ORDER BY e.id DESC LIMIT %s) r) AS events_json"""
    _TASK_WHERE = " WHERE t.task_id=%s AND t.deleted_at IS NULL"
    _ARTIFACTS_SUBQUERY = """
  (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.generation DESC,a.id)::text,'[]')
     FROM task_artifacts a WHERE a.task_id=t.task_id) AS artifacts_json"""
    _MANIFEST_SUBQUERY = """
  (SELECT jsonb_build_object('revision_id',m.revision_id,'revision_hash',m.revision_hash,
     'rows',(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.phase_key,a.name,a.id),'[]'::jsonb)
       FROM task_artifacts a WHERE a.task_id=t.task_id AND a.generation=t.generation AND a.publication_state='published'))::text
   FROM artifact_manifest_ready m WHERE m.task_id=t.task_id AND m.generation=t.generation) AS manifest_json"""

    def _snapshot_artifacts_sql(self):
        from . import config
        return self._ARTIFACTS_SUBQUERY + (",\n" + self._MANIFEST_SUBQUERY if config.ARTIFACT_MANIFEST_READS else "")

    def list_tasks(self):
        rows=self._db.execute(self._TASK_PROJECTION+' WHERE t.deleted_at IS NULL ORDER BY t.updated_at DESC').fetchall()
        return [self._task_record(row) for row in rows]

    def task(self,task_id):
        row=self._db.execute(self._TASK_PROJECTION+' WHERE t.task_id=%s AND t.deleted_at IS NULL',(task_id,)).fetchone()
        return self._task_record(row) if row else None

    def _execute_native(self, sql, parameters=(), *, write=False):
        """PG-native statement under the owning operation transaction, no dialect shim."""
        connection = self._connection.get()
        if connection is None:
            raise TenantAccessDenied('scoped transaction required')
        if write:
            from . import task_read_cache
            from .postgres_scope import _request_tx
            task_read_cache.invalidate(self)
            self._cache_dirty.set(True)
            shared = _request_tx.get()
            if shared is not None:
                shared.cache_invalidations.add(self)
        started = time.monotonic()
        try:
            return connection.execute(sql, parameters)
        finally:
            note_query(time.monotonic()-started)

    def task_snapshot(self, task_id, event_limit=500):
        """One PG statement for task, ordered bounded events and artifact evidence."""
        artifact_sql = self._snapshot_artifacts_sql()
        sql = self._TASK_PROJECTION.replace("\nFROM tasks t", ",\n" + self._EVENTS_SUBQUERY
            + (",\n" + artifact_sql if artifact_sql else "") + "\nFROM tasks t") + self._TASK_WHERE
        row = self._execute_native(sql, (event_limit, task_id)).fetchone()
        if row is None:
            return None
        item = dict(row)
        events_json = item.pop('events_json', '[]')
        artifacts = json.loads(item.pop('artifacts_json', '[]') or '[]')
        manifest = json.loads(item.pop('manifest_json', None) or 'null')
        if manifest is not None and self._revision_hash(manifest['rows']) != manifest['revision_hash']:
            raise ValueError('readiness marker does not match the published manifest revision')
        return {'task':self._task_record(item),
                'events':[self._event_record(e) for e in reversed(json.loads(events_json or '[]'))],
                'artifacts':artifacts, 'manifest':manifest}

    def rename_task(self, task_id, title, actor='local user'):
        import psycopg
        title = title.strip()
        if not title:
            raise ValueError('task name cannot be empty')
        if not self.task(task_id):
            raise KeyError(task_id)
        try:
            self._execute_native('UPDATE tasks SET title=%s,updated_at=%s WHERE task_id=%s',
                                 (title,time.time(),task_id), write=True)
            self.audit(actor,'task.renamed',task_id,{'title':title})
        except psycopg.errors.UniqueViolation as error:
            raise ValueError(f'a task named {title!r} already exists') from error
        return self.task(task_id)

    def task_artifacts(self, task_id, *, include_stale=True):
        where = 'task_id=%s' if include_stale else 'task_id=%s AND stale=0'
        return [dict(row) for row in self._execute_native(
            'SELECT * FROM task_artifacts WHERE '+where+' ORDER BY generation DESC,id', (task_id,))]

    def _gate_record(self, row):
        item = dict(row)
        item['options'] = json.loads(item['options'])
        return item

    def _event_record(self, item):
        item = dict(item)
        item['payload'] = json.loads(item['payload']) if isinstance(item.get('payload'),str) else item.get('payload',{})
        return item

    def request_task_stop(self,task_id):
        task=self._db.execute('SELECT * FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        if not task or task['deleted_at'] is not None:raise KeyError(task_id)
        if task['stop_requested_at'] is None:
            self._db.execute("UPDATE tasks SET state='stop_requested',generation=generation+1,stop_requested_at=?,updated_at=? WHERE task_id=?",(time.time(),time.time(),task_id))
        return self.task(task_id)

    def confirm_task_stopped(self,task_id,*,engine_quiescent):
        from .attempt_ledger import AdmissionDenied
        if engine_quiescent is not True:raise AdmissionDenied('engine quiescence required')
        task=self._db.execute('SELECT * FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        if not task or task['stop_requested_at'] is None:raise AdmissionDenied('durable stop request required')
        if self._db.execute("SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=? AND p.state IN ('dispatched','uncertain','reserved') LIMIT 1",(task_id,)).fetchone():
            raise AdmissionDenied('provider attempt is not reconciled')
        # Closed work stays for accounting. Pending dispatch is fenced.
        self._db.execute("UPDATE worker_jobs SET state='done',updated_at=? WHERE task_id=? AND state IN ('pending','paused')",(time.time(),task_id))
        if self._db.execute("SELECT 1 FROM worker_jobs WHERE task_id=? AND state='running' LIMIT 1",(task_id,)).fetchone():
            raise AdmissionDenied('worker remains active')
        if task['stopped_at'] is None:
            self._db.execute("UPDATE tasks SET state='stopped',stopped_at=?,updated_at=? WHERE task_id=?",(time.time(),time.time(),task_id))
        return self.task(task_id)

    def tombstone_stopped_task(self,task_id):
        from .attempt_ledger import AdmissionDenied
        task=self._db.execute('SELECT * FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        if not task or task['state']!='stopped' or task['stopped_at'] is None:
            raise AdmissionDenied('confirmed stopped task required')
        # Preserve FK chain, raw usage, charges and existing source artifacts.
        if task['deleted_at'] is None:
            self._db.execute('UPDATE tasks SET deleted_at=?,updated_at=? WHERE task_id=?',(time.time(),time.time(),task_id))
        return self._task_record(self._db.execute(self._TASK_PROJECTION+' WHERE t.task_id=?',(task_id,)).fetchone())

    def delete_task(self,task_id,actor='local user'):
        task=self.task(task_id)
        if not task:raise KeyError(task_id)
        if task['state']=='stopped':
            self.tombstone_stopped_task(task_id)
            self.audit(actor,'task.deleted',task_id,{'title':task['title'],'source_artifacts_deleted':False,'accounting_retained':True})
            return
        if task['stop_requested_at'] is not None:
            raise ValueError('Stop is pending; worker and provider quiescence must be confirmed before Delete')
        if self._db.execute('SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=? LIMIT 1',(task_id,)).fetchone():
            raise ValueError('task has provider ledger history; preserve it for reconciliation')
        task=self.task(task_id)
        if not task: raise KeyError(task_id)
        if task['state'] in ('running','waiting_gate'):
            raise ValueError('a running or paused task cannot be deleted')
        if (self._db.execute('SELECT 1 FROM retail_compute_segments r JOIN worker_jobs j ON j.job_id=r.job_id WHERE j.task_id=? LIMIT 1',(task_id,)).fetchone()
                or self._db.execute('SELECT 1 FROM retail_contributions r JOIN worker_jobs j ON j.job_id=r.job_id WHERE j.task_id=? LIMIT 1',(task_id,)).fetchone()):
            # Compute and token accounting rows reference the task's jobs: hide the task, keep the rows.
            self._db.execute('UPDATE tasks SET deleted_at=?,updated_at=? WHERE task_id=? AND deleted_at IS NULL',(time.time(),time.time(),task_id))
            self.audit(actor,'task.deleted',task_id,{'title':task['title'],'source_artifacts_deleted':False,'accounting_retained':True})
            return
        self.audit(actor,'task.deleted',task_id,{'title':task['title'],'source_artifacts_deleted':False})
        for table in ('worker_jobs','engine_sessions','review_comments','decision_reviews','decision_drafts',
                      'answer_feedback','phases','gates','events','progress_ingest','progress_seen','task_artifacts'):
            self._db.execute(f'DELETE FROM {table} WHERE task_id=?',(task_id,))
        self._db.execute('DELETE FROM tasks WHERE task_id=?',(task_id,))

    def set_engine_session(self,task_id,session_id):
        task=self.task(task_id)
        if not task: raise KeyError(task_id)
        existing=self._db.execute('SELECT * FROM engine_sessions WHERE session_id=?',(session_id,)).fetchone()
        if existing and (existing['task_id'] != task_id or existing['generation'] != task['generation']):
            raise TenantAccessDenied('engine session binding is immutable')
        if not existing:
            self._db.execute('INSERT INTO engine_sessions VALUES (?,?,?,?,?)',
                (session_id,current_scope().owner_id,task_id,task['generation'],time.time()))
        self._db.execute('UPDATE tasks SET engine_session_id=?,updated_at=? WHERE task_id=?',(session_id,time.time(),task_id))

    def begin_interview_start(self,task_id,session_id,route):
        if not super().begin_interview_start(task_id,session_id,route): return False
        self.set_engine_session(task_id,session_id)
        return True

    def session_binding(self,session_id):
        row=self._db.execute('SELECT * FROM engine_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not row:return None
        result=dict(row);result['owner_id']=str(result['owner_id']);return result

    def installed_execution_anchor(self,session_id):
        """Read exact immutable installed binding, no file-derived authority."""
        from vmodel_contracts.execution_anchor import ExecutionAnchor
        import hashlib
        row=self._db.execute('SELECT * FROM installed_execution_bindings WHERE session_id=?',(session_id,)).fetchone()
        if not row:return None
        intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=?',(row['installation_id'],)).fetchone()
        latest=self._db.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',(row['installation_id'],)).fetchone()
        if not intent or not latest or latest['state']!='installed':raise TenantAccessDenied('installed execution journal unavailable')
        payload=json.loads(latest['payload']);plan=json.loads(intent['payload'])
        if (hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=latest['event_sha256']
            or hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=intent['plan_sha256']
            or payload['plan_sha256']!=intent['plan_sha256'] or payload['evidence']['execution_session_id']!=session_id
            or payload['evidence']['anchor_sha256']!=row['anchor_sha256']):
            raise TenantAccessDenied('installed execution evidence differs from immutable journal')
        anchor=ExecutionAnchor(str(row['owner_id']),row['task_id'],session_id,row['semantic_session_id'],row['generation'],row['lineage_sha256'],row['installation_id'])
        if plan['destination_anchor']!=anchor.document():raise TenantAccessDenied('installed execution anchor differs from plan')
        return {'anchor':anchor,'anchor_sha256':row['anchor_sha256']}

    def _task_record(self,row):
        result = dict(row)
        result['phases'] = json.loads(result.pop('phases_json'))
        result['gates'] = [self._gate_record(g) for g in json.loads(result.pop('gates_json'))]
        result['artifact_count'] = int(result['artifact_count'])
        result['owner_id'] = str(result['owner_id'])
        return result

    def enqueue_worker(self,task_id,kind,*,execution_options=None):
        if kind=='proposal:closed-gate-revision':
            from .closed_revision_job import _options
            _options(execution_options)
        from .worker_capability import WorkerCapability
        capability=WorkerCapability.capture(self,task_id)
        capability.validate(self)
        if not capability.scope.session_id:raise TenantAccessDenied('worker auth session required')
        job_id=secrets.token_hex(16);now=time.time()
        self._db.execute("INSERT INTO worker_jobs (job_id,owner_id,auth_session_id,task_id,session_id,generation,kind,state,attempt,created_at,updated_at,execution_options) VALUES (?,?,?,?,?,?,?,'pending',0,?,?,?::jsonb)",
           (job_id,capability.scope.owner_id,capability.scope.session_id,task_id,
            capability.session_id,capability.generation,kind,now,now,json.dumps(execution_options or {})))
        return job_id

    def prepare_phase_retry(self,task_id,phase,actor,model,checkpoint):
        """One durable transaction for failed-phase claim, model and pending job.

        Lock the owned task before checking eligibility so concurrent confirms
        cannot each reserve a job. Public operation wrapper owns commit/rollback.
        """
        row=self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        if not row:return None
        claimed=(self.claim_failed_needs_retry(task_id,actor) if phase=='needs'
                 else self.claim_failed_phase_retry(task_id,phase,actor))
        if not claimed:return None
        self.set_model_default(task_id,model)
        self.add_event(task_id,'model_route',f'{phase} retry model selected: {model}',phase_key=phase,
          payload={'route':model.split(':',1)[0],'resolved':model,'source':'explicit_retry_choice'})
        self.audit(actor,'phase.retry_model',task_id,{'phase':phase,'model':model,'checkpoint':checkpoint})
        return self.enqueue_worker(task_id,'phase:'+phase,execution_options={
            'mode':'resume' if checkpoint else 'start','model_route':model})

    def prepare_next_phase(self,task_id,phase,actor,model):
        """One durable transaction for the first start of a generator phase: claim, model and pending job."""
        row=self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        if not row:return None
        if not self.claim_next_phase(task_id,phase,actor):return None
        self.set_model_default(task_id,model)
        self.add_event(task_id,'model_route',f'{phase} model: {model}',phase_key=phase,
          payload={'route':model.split(':',1)[0],'resolved':model,'source':'interview_session_default'})
        return self.enqueue_worker(task_id,'phase:'+phase,execution_options={'mode':'start','model_route':model})

    def retain_closed_revision_restart_intent(self,task_id,*,request_key,options,proposal,
                                             approval_reference,read_current_evidence):
        """Atomically retain a held intent with owner/task CAS, never dispatch.

        Trusted caller authenticates the approval and freshly loaded proposal.
        Evidence callback runs under owner/task transaction locks; it must read
        current artifacts and checkpoint under the same-host writer lease.
        """
        from .revision_intent import intent_payload
        from .gate_revision import closed_revision_preflight
        if not isinstance(request_key,str) or not 1<=len(request_key)<=200:
            raise ValueError('bounded replay-safe request key required')
        if not callable(read_current_evidence):raise ValueError('trusted current evidence reader required')
        body,fingerprint=intent_payload(options=options,proposal=proposal,approval_reference=approval_reference)
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            task=self.task(task_id);scope=current_scope()
            if task['generation']!=proposal['generation'] or proposal['task_id']!=task_id:
                raise ValueError('reviewed restart generation/task changed')
            binding=self.session_binding(proposal['session_id'])
            if not binding or binding['task_id']!=task_id or binding['generation']!=task['generation']:
                raise TenantAccessDenied('reviewed restart session changed')
            prior=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE owner_id=? AND request_key=?',
                (scope.owner_id,request_key)).fetchone()
            if prior:
                if prior['fingerprint']!=fingerprint:raise ValueError('conflicting restart intent replay')
                return {'intent_id':prior['intent_id'],'state':'held','replayed':True,'dispatch_started':False}
            def compare():
                evidence=read_current_evidence()
                if not isinstance(evidence,dict) or set(evidence)!={'artifacts','checkpoint_id','proposal_sha256'}:
                    raise ValueError('exact current restart evidence required')
                if evidence['checkpoint_id']!=options['source_checkpoint_id'] or evidence['proposal_sha256']!=proposal['artifact_sha256']:
                    raise ValueError('reviewed checkpoint/proposal evidence changed')
                live=closed_revision_preflight(self,task_id,options['gate_key'],options['review_revision'],
                    options['draft_id'],artifact_snapshot=evidence['artifacts'])
                if live['review_digest']!=options['preflight_digest']:raise ValueError('reviewed artifact/gate evidence changed')
            compare();compare()
            intent_id=secrets.token_hex(16)
            self._db.execute('INSERT INTO closed_revision_restart_intents(intent_id,request_key,owner_id,auth_session_id,task_id,source_generation,gate_key,fingerprint,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (intent_id,request_key,scope.owner_id,scope.session_id,task_id,task['generation'],options['gate_key'],fingerprint,json.dumps(body,sort_keys=True),time.time()))
            self.audit(scope.owner_id,'gate.revision_restart_intent_held',task_id,
                {'intent_id':intent_id,'source_generation':task['generation'],'review_digest':options['preflight_digest']})
            return {'intent_id':intent_id,'state':'held','replayed':False,'dispatch_started':False}

    def retain_closed_revision_lineage_plan(self,task_id,*,intent_id,semantic_session_id,
                                            new_execution_session_id,retained_proposal):
        """Retain lineage planning only; session binding and generation unchanged.

        Trusted caller reads semantic id from approved source lineage and proposal
        from the pinned owner reader. This does not approve installation.
        """
        from .worker_capability import WorkerCapability
        from .revision_lineage import plan_revision_lineage
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            intent=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=? AND task_id=?',
                (intent_id,task_id)).fetchone()
            if not intent:raise KeyError(intent_id)
            held=json.loads(intent['payload'])
            if held['state']!='held' or held['proposal']!=retained_proposal.get('proposal'):
                raise ValueError('lineage proposal differs from held reviewed intent')
            planned=plan_revision_lineage(capability=capability,semantic_session_id=semantic_session_id,
                new_execution_session_id=new_execution_session_id,options=held['options'],retained_proposal=retained_proposal)
            prior=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE intent_id=?',(intent_id,)).fetchone()
            if prior:
                if prior['fingerprint']!=planned['lineage_sha256']:raise ValueError('conflicting lineage plan replay')
                return {'lineage_id':prior['lineage_id'],'state':'planned','replayed':True,'installed':False}
            if self.session_binding(new_execution_session_id):raise ValueError('destination execution session already bound')
            scope=current_scope();lineage_id=secrets.token_hex(16);body=planned['lineage']
            self._db.execute('INSERT INTO closed_revision_lineage_plans(lineage_id,owner_id,auth_session_id,intent_id,task_id,parent_execution_session_id,parent_generation,execution_session_id,generation,semantic_session_id,fingerprint,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (lineage_id,scope.owner_id,scope.session_id,intent_id,task_id,capability.session_id,capability.generation,
                 new_execution_session_id,body['generation'],semantic_session_id,planned['lineage_sha256'],json.dumps(body,sort_keys=True),time.time()))
            self.audit(scope.owner_id,'gate.revision_lineage_planned',task_id,{'intent_id':intent_id,'lineage_id':lineage_id})
            return {'lineage_id':lineage_id,'state':'planned','replayed':False,'installed':False}

    def append_closed_revision_preparation_event(self,task_id,*,lineage_id,expected_previous_sha256,
                                               state,evidence,read_staged_receipt=None):
        """Append preparation-only journal. No promotion/install/dispatch states.

        Staged receipts are loaded from trusted source reads, not request flags.
        These file/DB checks are not a filesystem transaction or install grant.
        """
        from .revision_journal import journal_event
        if state not in ('prepared','staged','held_for_review'):
            raise ValueError('publication and installation journal transitions unavailable')
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            lineage=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',
                (lineage_id,task_id)).fetchone()
            if not lineage:raise KeyError(lineage_id)
            task=self.task(task_id)
            if task['generation']!=lineage['parent_generation'] or task['engine_session_id']!=lineage['parent_execution_session_id']:
                raise ValueError('journal parent generation/session changed')
            latest=self._db.execute('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=? ORDER BY sequence DESC LIMIT 1',
                (lineage_id,)).fetchone()
            previous={'event':json.loads(latest['payload']),'event_sha256':latest['event_sha256']} if latest else None
            if (latest['event_sha256'] if latest else None)!=expected_previous_sha256:
                raise ValueError('journal compare-and-swap changed')
            if state=='prepared' and evidence!={'intent_id':lineage['intent_id'],'lineage_id':lineage_id}:
                raise ValueError('prepared journal identity mismatch')
            event=journal_event(lineage_sha256=lineage['fingerprint'],previous=previous,state=state,evidence=evidence)
            if state=='staged':
                if not callable(read_staged_receipt):raise ValueError('trusted staged receipt reader required')
                for _ in range(2):
                    receipt=read_staged_receipt()
                    if receipt!=evidence:raise ValueError('staged receipt evidence changed')
            scope=current_scope();body=event['event']
            self._db.execute('INSERT INTO closed_revision_promotion_events(lineage_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (lineage_id,scope.owner_id,scope.session_id,body['sequence'],state,event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),time.time()))
            self.audit(scope.owner_id,'gate.revision_preparation_journal',task_id,{'lineage_id':lineage_id,'state':state,'event_sha256':event['event_sha256']})
            return event

    def reconcile_private_revision_installation(self,task_id,*,installation_id,artifacts_base):
        """Concrete read-only current files/DB classification, never resumes."""
        from .revision_install_recovery import classify_installation_recovery
        from .revision_install_files import _validate_plan
        from .revision_stage_evidence import read_validated_staged_receipt
        from .tenancy import session_root
        from .producer_inputs import _open_directory
        from vmodel_contracts.execution_anchor import read_execution_anchor
        from pathlib import Path
        from types import SimpleNamespace
        import os
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=? AND task_id=?',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
            event=self._db.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
            if not event:raise ValueError('installation journal missing')
            import hashlib
            journal=json.loads(event['payload'])
            if (hashlib.sha256(json.dumps(journal,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=event['event_sha256']
                or journal['plan_sha256']!=intent['plan_sha256']):raise ValueError('reconciliation journal digest changed')
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=?',(intent['lineage_id'],)).fetchone()
            held=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=?',(row['intent_id'],)).fetchone()
            lineage=json.loads(row['payload']);options=json.loads(held['payload'])['options'];scope=current_scope()
            source=session_root(Path(artifacts_base),scope,lineage['parent_execution_session_id'])
            stage=source/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/anchor.execution_session_id
            destination=session_root(Path(artifacts_base),scope,anchor.execution_session_id)
            def contained_present(path):
                try:fd=_open_directory(path)
                except FileNotFoundError:return False
                else:os.close(fd);return True
            errors=[]
            try:stage_present=contained_present(stage)
            except (OSError,ValueError):stage_present=False;errors.append('private_stage_namespace_unverified')
            # Historical binding is read-only evidence, never active worker
            # authority or a capability that can run models after installation.
            def historical_check(store):
                if current_scope()!=scope or not self.auth_session_active():raise TenantAccessDenied('reconciliation owner revoked')
                binding=self.session_binding(lineage['parent_execution_session_id'])
                if not binding or binding['task_id']!=task_id or binding['owner_id']!=scope.owner_id or binding['generation']!=lineage['parent_generation']:
                    raise TenantAccessDenied('historical source binding changed')
            historical=SimpleNamespace(scope=scope,task_id=task_id,session_id=lineage['parent_execution_session_id'],generation=lineage['parent_generation'],validate=historical_check)
            files=False
            try:
                if contained_present(destination):
                    from .revision_source_review import read_source_review_snapshot
                    retained=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(intent['lineage_id'],)).fetchone()
                    snapshot=read_source_review_snapshot(self,historical,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=row['fingerprint'])
                    if not retained or snapshot!={'snapshot':json.loads(retained['payload']),'snapshot_sha256':retained['fingerprint']}:
                        raise ValueError('reconciliation source differs from retained review')
                    proof=read_execution_anchor(destination,expected=anchor)
                    receipt=read_validated_staged_receipt(self,historical,artifacts_base=artifacts_base,lineage=lineage,
                        lineage_sha256=row['fingerprint'],manifest_sha256=plan['staged_receipt']['manifest_sha256'],options=options,promoted=True)
                    if receipt!=plan['staged_receipt']:raise ValueError('reconciliation manifest differs')
                    expected=json.loads(event['payload'])['evidence'].get('anchor_sha256')
                    if expected!=proof['document_sha256']:raise ValueError('reconciliation anchor differs from journal')
                    files=True
            except (OSError,ValueError,KeyError):errors.append('promoted_file_evidence_unverified')
            task=self.task(task_id);installed=False
            if task['generation']==anchor.generation and task['engine_session_id']==anchor.execution_session_id:
                try:
                    installed=self.installed_execution_anchor(anchor.execution_session_id)=={'anchor':anchor,
                        'anchor_sha256':json.loads(event['payload'])['evidence'].get('anchor_sha256')}
                except (ValueError,KeyError):errors.append('installed_database_evidence_unverified')
            result=classify_installation_recovery(journal_state=event['state'],private_stage_present=stage_present,
                promoted_files_verified=files,database_installed=installed)
            return result|{'unverified':errors}

    def read_promoted_installation_evidence(self,task_id,*,installation_id,artifacts_base):
        """Concrete original+promoted typed receipt, not DB installation."""
        from .worker_capability import WorkerCapability
        from .revision_source_review import read_source_review_snapshot
        from .revision_proposal import read_retained_revision_proposal
        from .revision_stage_evidence import read_validated_staged_receipt
        from .revision_install_files import _validate_plan
        from .tenancy import session_root
        from vmodel_contracts.execution_anchor import read_execution_anchor
        from pathlib import Path
        cap=WorkerCapability.capture(self,task_id);cap.validate(self)
        intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=? AND task_id=?',(installation_id,task_id)).fetchone()
        if not intent:raise KeyError(installation_id)
        plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
        row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=?',(intent['lineage_id'],)).fetchone()
        source=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(intent['lineage_id'],)).fetchone()
        held=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=?',(row['intent_id'],)).fetchone()
        lineage=json.loads(row['payload']);options=json.loads(held['payload'])['options']
        from .revision_evidence import read_revision_artifacts
        from .gate_revision import closed_revision_preflight
        original=session_root(Path(artifacts_base),cap.scope,cap.session_id)
        phase_evidence=read_revision_artifacts(self,cap,original,lineage['phase'],artifacts_base=artifacts_base)
        preflight=closed_revision_preflight(self,task_id,options['gate_key'],options['review_revision'],options['draft_id'],artifact_snapshot=phase_evidence)
        if preflight['review_digest']!=options['preflight_digest']:raise ValueError('promoted installation gate/draft basis changed')
        snapshot=read_source_review_snapshot(self,cap,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=row['fingerprint'])
        if snapshot!={'snapshot':json.loads(source['payload']),'snapshot_sha256':source['fingerprint']}:
            raise ValueError('promoted installation source review changed')
        proposal=read_retained_revision_proposal(self,cap,session_root(Path(artifacts_base),cap.scope,cap.session_id),lineage['proposal_id'],artifacts_base=artifacts_base,options=options)
        if proposal['document_sha256']!=lineage['proposal_document_sha256']:raise ValueError('promoted installation proposal changed')
        receipt=read_validated_staged_receipt(self,cap,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=row['fingerprint'],
            manifest_sha256=plan['staged_receipt']['manifest_sha256'],options=options,promoted=True)
        if receipt!=plan['staged_receipt']:raise ValueError('promoted receipt differs from reviewed plan')
        proof=read_execution_anchor(session_root(Path(artifacts_base),cap.scope,anchor.execution_session_id),expected=anchor)
        return {'manifest_sha256':receipt['manifest_sha256'],'anchor_sha256':proof['document_sha256']}

    def activate_private_revision_installation(self,task_id,*,installation_id,expected_previous_sha256,validate_acceptance,artifacts_base):
        """Private concrete DB CAS after promoted files, never dispatch.

        No route invokes this method. Owner acceptance verifier must authenticate
        retained exact review and cost acknowledgement; test fixtures aren't it.
        """
        from .revision_install_files import _validate_plan
        from .revision_install_journal import install_event
        from .phases import followers
        if not callable(validate_acceptance):raise ValueError('trusted acceptance verifier required')
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=? AND task_id=?',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
            latest=self._db.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
            if not latest or latest['state']!='install_pending' or latest['event_sha256']!=expected_previous_sha256:
                raise ValueError('exact install-pending journal CAS required')
            review=self.decision_review(task_id,'revision-install:'+intent['lineage_id'],intent['review_sha256'])
            if review is None:raise ValueError('retained accepted review basis missing')
            task=self.task(task_id);parent=plan['expected_parent']
            if task['generation']!=parent['generation'] or task['engine_session_id']!=parent['execution_session_id']:
                raise ValueError('installation parent generation/session changed')
            safety=self.inspect_revision_install_safety(task_id,lineage_id=intent['lineage_id'],artifacts_base=artifacts_base)
            if not safety['observed_work_quiescent']:raise ValueError('installation work quiescence unresolved')
            evidence=self.read_promoted_installation_evidence(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
            pending=json.loads(latest['payload'])
            import hashlib
            if (hashlib.sha256(json.dumps(pending,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=latest['event_sha256']
                or pending['plan_sha256']!=intent['plan_sha256'] or pending['evidence']!=evidence):
                raise ValueError('install-pending evidence changed')
            validate_acceptance(review,intent['review_sha256'],intent['acceptance_reference'])
            if self.read_promoted_installation_evidence(task_id,installation_id=installation_id,artifacts_base=artifacts_base)!=evidence:
                raise ValueError('promoted evidence changed before database CAS')
            if self.session_binding(anchor.execution_session_id):raise ValueError('installation destination already bound')
            scope=current_scope();now=time.time()
            self._db.execute('INSERT INTO engine_sessions VALUES (?,?,?,?,?)',(anchor.execution_session_id,scope.owner_id,task_id,anchor.generation,now))
            self._db.execute('INSERT INTO installed_execution_bindings(session_id,owner_id,auth_session_id,task_id,generation,semantic_session_id,installation_id,lineage_sha256,anchor_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (anchor.execution_session_id,scope.owner_id,scope.session_id,task_id,anchor.generation,anchor.semantic_session_id,installation_id,anchor.lineage_sha256,evidence['anchor_sha256'],now))
            changed=self._db.execute("UPDATE tasks SET engine_session_id=?,generation=?,state='pending',active_phase=?,updated_at=? WHERE task_id=? AND generation=? AND engine_session_id=?",
                (anchor.execution_session_id,anchor.generation,next(iter(followers(review['phase']))).key,now,task_id,parent['generation'],parent['execution_session_id']))
            if changed.rowcount!=1:raise ValueError('installation parent CAS failed')
            phase=review['phase'];later=[p.key for p in followers(phase)]
            self._db.execute("UPDATE phases SET state='approved',detail='Approved Exact Regenerated Result',completed_at=?,gate_basis_changed=1 WHERE task_id=? AND phase_key=?",(now,task_id,phase))
            self._db.execute("UPDATE gates SET state='decided',is_open=0,selected_option='approve',edited_text=NULL,steer=NULL,revision=revision+1 WHERE task_id=? AND after_phase=?",(task_id,phase))
            for key in later:
                self._db.execute("UPDATE phases SET state='pending',detail='',completed=0,total=NULL,started_at=NULL,completed_at=NULL,gate_basis_changed=1 WHERE task_id=? AND phase_key=?",(task_id,key))
                self._db.execute("UPDATE gates SET state='idle',is_open=0,selected_option=NULL,edited_text=NULL,steer=NULL,revision=revision+1 WHERE task_id=? AND after_phase=?",(task_id,key))
            for key in [phase]+later:
                self._db.execute("UPDATE task_artifacts SET stale=1,stale_reason='reviewed lineage regeneration' WHERE task_id=? AND phase_key=? AND generation=?",(task_id,key,parent['generation']))
            event=install_event(plan_sha256=intent['plan_sha256'],previous={'event':json.loads(latest['payload']),'event_sha256':latest['event_sha256']},
                state='installed',evidence=evidence|{'execution_session_id':anchor.execution_session_id})
            body=event['event']
            self._db.execute('INSERT INTO closed_revision_install_events(installation_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (installation_id,scope.owner_id,scope.session_id,body['sequence'],body['state'],event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),now))
            self.audit(scope.owner_id,'gate.private_revision_installed',task_id,{'installation_id':installation_id,'generation':anchor.generation,'approved_phase':phase,'review_sha256':intent['review_sha256'],
                'acceptance_reference':intent['acceptance_reference'],'next_phase':later[0] if later else None,'dispatch_started':False})
            return {'installation_id':installation_id,'event_sha256':event['event_sha256'],'installed':True,'dispatch_started':False,
                'approved_phase':phase,'next_phase':later[0] if later else None,'continuation_prepared':True}

    def private_approved_continuation_basis(self,task_id,*,installation_id,artifacts_base,expected_job_id=None):
        """Exact current DB approval and immutable installed files, no dispatch."""
        from .worker_capability import WorkerCapability
        from .revision_install_files import _validate_plan
        with self._task_operation_guard('task.rerun'):
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=? AND task_id=?',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
            if cap.execution_anchor!=anchor:raise ValueError('current installed continuation binding differs')
            review=self.decision_review(task_id,'revision-install:'+intent['lineage_id'],intent['review_sha256'])
            self.verify_private_revision_acceptance(review,intent['review_sha256'],intent['acceptance_reference'])
            task=cap.validate(self);phase=review['phase']
            gate=next((g for g in task['gates'] if g['after_phase']==phase),None)
            status=next((p for p in task['phases'] if p['phase_key']==phase),None)
            if not gate or gate['state']!='decided' or not status or status['state']!='approved':
                raise ValueError('exact installed edited phase approval required')
            if expected_job_id is not None:
                job=self.worker_job(expected_job_id)
                if (not job or job['task_id']!=task_id or job['session_id']!=cap.session_id
                    or job['generation']!=cap.generation or job['kind']!='continuation:approved-revision'
                    or job['state'] not in ('pending','running') or job['execution_options']!={}):
                    raise ValueError('exact private continuation worker required')
            if (self._db.execute("SELECT 1 FROM worker_jobs WHERE task_id=? AND state IN ('pending','running','paused','failed') AND job_id<>? LIMIT 1",(task_id,expected_job_id or '')).fetchone()
                or self._db.execute("SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=? AND p.state IN ('reserved','dispatched','uncertain') AND j.job_id<>? LIMIT 1",(task_id,expected_job_id or '')).fetchone()):
                raise ValueError('continuation requires quiescent jobs and providers')
            reconciled=self.reconcile_private_revision_installation(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
            if reconciled['state']!='installed_verified_no_dispatch' or reconciled['unverified']:
                raise ValueError('installed continuation file evidence unresolved')
            return {'capability':cap,'phase':phase,'artifact_sha256':review['regenerated_artifact_sha256'],
                'checkpoint_id':review['staged_receipt']['checkpoint_id'],'approval_reference':intent['acceptance_reference'],
                'review_sha256':intent['review_sha256'],'installation_id':installation_id}

    def prepare_private_revision_changes(self,task_id,*,lineage_id,expected_staged_event_sha256,review_sha256,change_request,extra_cost_acknowledged,artifacts_base):
        """Retain same-phase edit submission and fresh original-source preflight.

        No queue/provider call, install or replacement of reviewed bytes. Exact
        original guidance and new guidance are combined explicitly, so rejecting
        this proposal cannot silently discard the first requested change.
        """
        if (not isinstance(change_request,str) or not change_request.strip() or len(change_request)>20000
            or extra_cost_acknowledged is not True):raise ValueError('change guidance and explicit extra cost acknowledgement required')
        from .gate_revision import save_closed_revision,closed_revision_preflight
        from .closed_revision_job import _options
        with self._task_operation_guard('task.rerun'):
            current=self.retain_revision_installation_review(task_id,lineage_id=lineage_id,
                expected_staged_event_sha256=expected_staged_event_sha256,artifacts_base=artifacts_base)
            if current['review_sha256']!=review_sha256:raise ValueError('submitted review is stale')
            if self._db.execute('SELECT 1 FROM decision_drafts WHERE task_id=? AND wait_id=? AND revision=? LIMIT 1',(task_id,'revision-changes:'+lineage_id,review_sha256)).fetchone():
                raise ValueError('review superseded by further changes')
            scope=current_scope()
            if scope.session_id is None or not self.auth_session_active():raise TenantAccessDenied('active owner submission required')
            lineage=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',(lineage_id,task_id)).fetchone()
            intent=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=?',(lineage['intent_id'],)).fetchone()
            original=json.loads(intent['payload'])['options']
            combined='Original Requested Change:\n'+original['change_request']+'\n\nFurther Requested Change After Reviewing The Regenerated Result:\n'+change_request
            if len(combined)>20000:raise ValueError('combined original and further guidance exceeds supported limit')
            saved=save_closed_revision(self,task_id,original['gate_key'],original['review_revision'],combined)
            fresh=closed_revision_preflight(self,task_id,original['gate_key'],original['review_revision'],saved['draft_id'],artifact_snapshot=original['artifact_evidence'])
            options=_options(original|{'draft_id':saved['draft_id'],'preflight_digest':fresh['review_digest'],
                'change_request':combined,'extra_cost_acknowledged':True})
            payload={'review_sha256':review_sha256,'decision':'edit','change_request':change_request,
                'original_change_request':original['change_request'],'extra_cost_acknowledged':True,'options':options,
                'same_phase_review_required':True,'installed':False,'dispatch_started':False}
            submission=self.save_decision_draft(task_id,'revision-changes:'+lineage_id,review_sha256,payload)
            # Rejecting a reviewed result revokes any previous receipt for it.
            receipts=self._db.execute('SELECT acceptance_id FROM closed_revision_acceptances WHERE lineage_id=? AND review_sha256=?',(lineage_id,review_sha256)).fetchall()
            for receipt in receipts:self.revoke_private_revision_acceptance(task_id,acceptance_reference=receipt['acceptance_id'])
            return {'submission_id':submission,'review_sha256':review_sha256,'options':options,
                'same_phase_review_required':True,'installed':False,'dispatch_started':False}

    def record_private_revision_acceptance(self,task_id,*,lineage_id,expected_staged_event_sha256,review_sha256,decision,extra_cost_acknowledged,artifacts_base):
        """Private future authenticated submission seam, not wired to any route.

        Callable only from trusted backend after the owner submits this exact
        displayed review/choice/cost ack. A caller or fixture does not create
        real-world owner permission merely by invoking this method.
        """
        if decision!='approve' or extra_cost_acknowledged is not True:
            raise ValueError('explicit approval and extra cost acknowledgement required')
        with self._task_operation_guard('task.gate.decide'):
            current=self.retain_revision_installation_review(task_id,lineage_id=lineage_id,
                expected_staged_event_sha256=expected_staged_event_sha256,artifacts_base=artifacts_base)
            if current['review_sha256']!=review_sha256:raise ValueError('submitted review is stale')
            if self._db.execute('SELECT 1 FROM decision_drafts WHERE task_id=? AND wait_id=? AND revision=? LIMIT 1',(task_id,'revision-changes:'+lineage_id,review_sha256)).fetchone():
                raise ValueError('review superseded by further changes')
            scope=current_scope()
            if scope.session_id is None or not self.auth_session_active():raise TenantAccessDenied('active authenticated owner submission required')
            prior=self._db.execute('SELECT * FROM closed_revision_acceptances WHERE lineage_id=? AND review_sha256=? AND auth_session_id=?',
                (lineage_id,review_sha256,scope.session_id)).fetchone()
            if prior:
                self.verify_private_revision_acceptance(current['review'],review_sha256,prior['acceptance_id'])
                return {'acceptance_reference':prior['acceptance_id'],'review_sha256':review_sha256,'replayed':True,'installed':False}
            import uuid
            acceptance_id='accept-'+uuid.uuid4().hex
            payload={'owner_id':scope.owner_id,'task_id':task_id,'lineage_id':lineage_id,'auth_session_id':scope.session_id,
                'review_sha256':review_sha256,'decision':'approve','extra_cost_acknowledged':True,
                'source':'private_authenticated_submission','installed':False,'dispatch_started':False}
            self._db.execute('INSERT INTO closed_revision_acceptances(acceptance_id,lineage_id,owner_id,auth_session_id,task_id,review_sha256,decision,extra_cost_acknowledged,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (acceptance_id,lineage_id,scope.owner_id,scope.session_id,task_id,review_sha256,'approve',True,json.dumps(payload,sort_keys=True),time.time()))
            return {'acceptance_reference':acceptance_id,'review_sha256':review_sha256,'replayed':False,'installed':False}

    def verify_private_revision_acceptance(self,review,review_sha256,acceptance_reference):
        """Exact durable receipt verifier; current source checks belong to coordinator."""
        import hashlib
        scope=current_scope()
        if hashlib.sha256(json.dumps(review,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=review_sha256:
            raise ValueError('acceptance exact review required')
        row=self._db.execute('SELECT * FROM closed_revision_acceptances WHERE acceptance_id=?',(acceptance_reference,)).fetchone()
        if (not row or str(row['owner_id'])!=scope.owner_id or str(row['auth_session_id'])!=scope.session_id
            or row['task_id']!=review['task_id'] or row['review_sha256']!=review_sha256
            or review['owner_id']!=scope.owner_id or row['decision']!='approve' or row['extra_cost_acknowledged'] is not True
            or not self.auth_session_active()):raise TenantAccessDenied('current exact authenticated acceptance required')
        expected={'owner_id':scope.owner_id,'task_id':row['task_id'],'lineage_id':row['lineage_id'],'auth_session_id':scope.session_id,
            'review_sha256':review_sha256,'decision':'approve','extra_cost_acknowledged':True,
            'source':'private_authenticated_submission','installed':False,'dispatch_started':False}
        if json.loads(row['payload'])!=expected:raise ValueError('durable acceptance payload differs')
        if self._db.execute('SELECT 1 FROM closed_revision_acceptance_revocations WHERE acceptance_id=?',(acceptance_reference,)).fetchone():
            raise TenantAccessDenied('private acceptance revoked')
        if self._db.execute('SELECT 1 FROM decision_drafts WHERE task_id=? AND wait_id=? AND revision=? LIMIT 1',(row['task_id'],'revision-changes:'+row['lineage_id'],review_sha256)).fetchone():
            raise ValueError('review superseded by further changes')
        held=self.decision_review(row['task_id'],'revision-install:'+row['lineage_id'],review_sha256)
        if held!=review:raise ValueError('acceptance review is not retained')
        return True

    def revoke_private_revision_acceptance(self,task_id,*,acceptance_reference):
        with self._task_operation_guard('task.gate.decide'):
            row=self._db.execute('SELECT * FROM closed_revision_acceptances WHERE acceptance_id=? AND task_id=?',(acceptance_reference,task_id)).fetchone()
            if not row:raise KeyError(acceptance_reference)
            scope=current_scope()
            if not self.auth_session_active():raise TenantAccessDenied('active owner required')
            self._db.execute('INSERT OR IGNORE INTO closed_revision_acceptance_revocations(acceptance_id,owner_id,auth_session_id,created_at) VALUES (?,?,?,?)',
                (acceptance_reference,scope.owner_id,scope.session_id,time.time()))
            return {'revoked':True,'installed':False}

    def retain_private_installation_intent(self,task_id,*,lineage_id,expected_staged_event_sha256,review_sha256,
                                            installation_id,acceptance_reference,validate_acceptance,artifacts_base):
        """Private coordinator seam. No route/acceptance UI calls this method.

        Trusted callback must authenticate exact retained owner review and cost
        acknowledgement; string reference alone grants nothing. Real owner
        acceptance surface remains absent. Tests supply constructed authority.
        """
        from .revision_install_plan import prepare_installation_plan
        if not callable(validate_acceptance):raise ValueError('trusted acceptance verifier required')
        if not isinstance(acceptance_reference,str) or not acceptance_reference.strip() or len(acceptance_reference)>500:
            raise ValueError('exact authenticated acceptance reference required')
        with self._task_operation_guard('task.rerun'):
            current=self.retain_revision_installation_review(task_id,lineage_id=lineage_id,
                expected_staged_event_sha256=expected_staged_event_sha256,artifacts_base=artifacts_base)
            if current['review_sha256']!=review_sha256:raise ValueError('accepted installation review changed')
            validate_acceptance(current['review'],review_sha256,acceptance_reference)
            plan=prepare_installation_plan(**current,installation_id=installation_id)
            prior=self._db.execute('SELECT * FROM closed_revision_installations WHERE lineage_id=?',(lineage_id,)).fetchone()
            if prior:
                if prior['plan_sha256']!=plan['plan_sha256'] or prior['acceptance_reference']!=acceptance_reference:
                    raise ValueError('conflicting installation intent replay')
                return plan|{'replayed':True}
            validate_acceptance(current['review'],review_sha256,acceptance_reference)
            scope=current_scope()
            self._db.execute('INSERT INTO closed_revision_installations(installation_id,lineage_id,owner_id,auth_session_id,task_id,review_sha256,plan_sha256,acceptance_reference,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (installation_id,lineage_id,scope.owner_id,scope.session_id,task_id,review_sha256,plan['plan_sha256'],acceptance_reference,json.dumps(plan['plan'],sort_keys=True),time.time()))
            self.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=None,state='authorized',
                evidence={'review_sha256':review_sha256,'acceptance_reference':acceptance_reference})
            return plan|{'replayed':False}

    def append_private_installation_event(self,task_id,*,installation_id,expected_previous_sha256,state,evidence,read_current_evidence=None):
        """Append-only private install journal, never performs filesystem effects.

        Installed DB activation uses a separate concrete transaction method;
        arbitrary callers cannot append installed certification here.
        """
        from .revision_install_journal import install_event
        if state=='installed':raise ValueError('installed certification requires concrete database activation')
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._db.execute('SELECT * FROM closed_revision_installations WHERE installation_id=? AND task_id=?',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);task=self.task(task_id);parent=plan['expected_parent']
            if task['generation']!=parent['generation'] or task['engine_session_id']!=parent['execution_session_id']:
                raise ValueError('installation parent generation/session changed')
            latest=self._db.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
            if (latest['event_sha256'] if latest else None)!=expected_previous_sha256:raise ValueError('installation journal CAS changed')
            previous={'event':json.loads(latest['payload']),'event_sha256':latest['event_sha256']} if latest else None
            if state=='authorized' and evidence!={'review_sha256':intent['review_sha256'],'acceptance_reference':intent['acceptance_reference']}:
                raise ValueError('installation authorization differs from retained intent')
            event=install_event(plan_sha256=intent['plan_sha256'],previous=previous,state=state,evidence=evidence)
            if state in ('promotion_pending','files_promoted','install_pending'):
                if not callable(read_current_evidence):raise ValueError('trusted current installation evidence reader required')
                if evidence['manifest_sha256']!=plan['staged_receipt']['manifest_sha256']:raise ValueError('installation manifest differs from held plan')
                for _ in range(2):
                    if read_current_evidence()!=evidence:raise ValueError('installation evidence changed')
            scope=current_scope();body=event['event']
            self._db.execute('INSERT INTO closed_revision_install_events(installation_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (installation_id,scope.owner_id,scope.session_id,body['sequence'],state,event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),time.time()))
            return event

    def inspect_revision_install_safety(self,task_id,*,lineage_id,artifacts_base):
        """Read current DB work and held source fence, never approve/install.

        No scanning global owners or interpreting timeout as thread exit. Caller
        must own the original cooperative writer scope; absent scope is reported
        as unverified, not acquired or converted into an installation grant.
        """
        from .revision_install_safety import installation_safety_snapshot
        from .tenancy import session_root
        from pathlib import Path
        from spec_generator.cooperative_run import cli_stop_binding
        binding=cli_stop_binding()
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            task=self.task(task_id);scope=current_scope()
            parent_current=(task['generation']==row['parent_generation'] and task['engine_session_id']==row['parent_execution_session_id'])
            root=session_root(Path(artifacts_base),scope,row['parent_execution_session_id'])
            writer=False
            if (binding is not None and binding[1]==row['parent_generation']
                and Path(binding[0].path)==root/'interview'/'stop-request.json'):
                binding[0].check(binding[1])
                writer=binding[0].active==1 and binding[0]._writer_file is not None
            jobs=self._db.execute("SELECT job_id FROM worker_jobs WHERE task_id=? AND state IN ('pending','running','paused','failed') ORDER BY job_id",(task_id,)).fetchall()
            attempts=self._db.execute("SELECT p.attempt_id FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=? AND p.state IN ('reserved','dispatched','uncertain') ORDER BY p.attempt_id",(task_id,)).fetchall()
            return installation_safety_snapshot(parent_current=parent_current,source_writer_excluded=writer,
                unresolved_jobs=[r['job_id'] for r in jobs],unresolved_attempts=[r['attempt_id'] for r in attempts])

    def retain_revision_installation_review(self,task_id,*,lineage_id,expected_staged_event_sha256,artifacts_base):
        """Retain exact current installation review basis, not acceptance.

        Caller holds original source and private destination writer exclusion.
        No action route, approval flag or install permission is created here.
        """
        from .worker_capability import WorkerCapability
        from .revision_install_review import installation_review_payload
        from .revision_source_review import read_source_review_snapshot
        from .revision_proposal import read_retained_revision_proposal
        from .revision_stage_evidence import read_validated_staged_receipt
        from .revision_evidence import read_revision_artifacts
        from .gate_revision import closed_revision_preflight
        from .tenancy import session_root
        from pathlib import Path
        import hashlib
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            event=self._db.execute('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=? ORDER BY sequence DESC LIMIT 1',(lineage_id,)).fetchone()
            if not event or event['state']!='staged' or event['event_sha256']!=expected_staged_event_sha256:
                raise ValueError('exact current staged journal required for review')
            payload=json.loads(event['payload'])
            if hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=event['event_sha256']:
                raise ValueError('staged event digest differs from payload')
            source=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(lineage_id,)).fetchone()
            intent=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=? AND task_id=?',(row['intent_id'],task_id)).fetchone()
            if not source or not intent:raise ValueError('held source review and intent required')
            lineage=json.loads(row['payload']);options=json.loads(intent['payload'])['options']
            root=session_root(Path(artifacts_base),cap.scope,cap.session_id)
            def current():
                cap.validate(self)
                snapshot=read_source_review_snapshot(self,cap,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=row['fingerprint'])
                if snapshot!={'snapshot':json.loads(source['payload']),'snapshot_sha256':source['fingerprint']}:
                    raise ValueError('installation review source differs from retained snapshot')
                proposal=read_retained_revision_proposal(self,cap,root,lineage['proposal_id'],artifacts_base=artifacts_base,options=options)
                receipt=read_validated_staged_receipt(self,cap,artifacts_base=artifacts_base,lineage=lineage,
                    lineage_sha256=row['fingerprint'],manifest_sha256=payload['evidence']['manifest_sha256'],options=options)
                if receipt!=payload['evidence']:raise ValueError('installation review receipt differs from staged journal')
                evidence=read_revision_artifacts(self,cap,root,lineage['phase'],artifacts_base=artifacts_base)
                preflight=closed_revision_preflight(self,task_id,options['gate_key'],options['review_revision'],options['draft_id'],artifact_snapshot=evidence)
                if preflight['review_digest']!=options['preflight_digest']:raise ValueError('installation review gate/draft basis changed')
                return installation_review_payload(lineage=lineage,lineage_sha256=row['fingerprint'],
                    proposal_document_sha256=proposal['document_sha256'],source_snapshot_sha256=source['fingerprint'],
                    staged_receipt=receipt,staged_event_sha256=event['event_sha256'])
            review=current()
            if current()!=review:raise ValueError('installation review evidence changed across retention')
            self.retain_decision_review(task_id,'revision-install:'+lineage_id,review['review_sha256'],review['review'])
            return review

    def prepare_revision_staging(self,task_id,*,lineage_id,expected_previous_sha256,artifacts_base):
        """Prepare private files and append staged receipt, never install.

        Called only inside captured original-source cooperative writer scope.
        Filesystem failure retains private partial evidence for explicit review;
        DB event is atomic, not a cross-filesystem/database transaction.
        """
        from .worker_capability import WorkerCapability
        from .revision_stage_writer import prepare_private_revision_stage
        from .gate_revision import closed_revision_preflight
        from .revision_evidence import read_revision_artifacts
        from .tenancy import session_root
        from pathlib import Path
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            latest=self._db.execute('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=? ORDER BY sequence DESC LIMIT 1',(lineage_id,)).fetchone()
            if not latest or latest['state']!='prepared' or latest['event_sha256']!=expected_previous_sha256:
                raise ValueError('exact prepared staging journal CAS required')
            review=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(lineage_id,)).fetchone()
            if not review:raise ValueError('retained upstream review snapshot required before staging')
            intent=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=? AND task_id=?',(row['intent_id'],task_id)).fetchone()
            if not intent:raise KeyError(row['intent_id'])
            options=json.loads(intent['payload'])['options'];lineage=json.loads(row['payload'])
            snapshot={'snapshot':json.loads(review['payload']),'snapshot_sha256':review['fingerprint']}
            def current():
                cap.validate(self)
                evidence=read_revision_artifacts(self,cap,session_root(Path(artifacts_base),cap.scope,cap.session_id),lineage['phase'],artifacts_base=artifacts_base)
                live=closed_revision_preflight(self,task_id,options['gate_key'],options['review_revision'],options['draft_id'],artifact_snapshot=evidence)
                if live['review_digest']!=options['preflight_digest']:raise ValueError('reviewed staging gate/draft evidence changed')
            current()
            receipt=prepare_private_revision_stage(self,cap,artifacts_base=artifacts_base,lineage=lineage,
                lineage_sha256=row['fingerprint'],options=options,review_snapshot=snapshot)
            current()
            return self.record_validated_revision_staging(task_id,lineage_id=lineage_id,expected_previous_sha256=expected_previous_sha256,
                manifest_sha256=receipt['manifest_sha256'],artifacts_base=artifacts_base)

    def record_validated_revision_staging(self,task_id,*,lineage_id,expected_previous_sha256,
                                          manifest_sha256,artifacts_base):
        """Bind concrete pinned/typed staged receipt to held journal, no install.

        artifacts_base is server configuration, never client-controlled. Caller
        holds the same-host writer lease for source and private stage namespace.
        """
        from .worker_capability import WorkerCapability
        from .revision_stage_evidence import read_validated_staged_receipt
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',
                (lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            intent=self._db.execute('SELECT * FROM closed_revision_restart_intents WHERE intent_id=? AND task_id=?',
                (row['intent_id'],task_id)).fetchone()
            if not intent:raise KeyError(row['intent_id'])
            lineage=json.loads(row['payload']);held=json.loads(intent['payload'])
            from .revision_source_review import read_source_review_snapshot,source_review_payload
            review=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(lineage_id,)).fetchone()
            if not review:raise ValueError('retained upstream review snapshot required before staging')
            saved=json.loads(review['payload'])
            expected=source_review_payload(lineage_sha256=row['fingerprint'],checkpoint_id=lineage['source_checkpoint_id'],
                semantic_session_id=lineage['semantic_session_id'],phase=lineage['phase'],upstream_sha256=saved['upstream_sha256'])
            if saved!=expected['snapshot'] or review['fingerprint']!=expected['snapshot_sha256']:
                raise ValueError('retained upstream snapshot identity/digest changed')
            def compare_source():
                live=read_source_review_snapshot(self,capability,artifacts_base=artifacts_base,
                    lineage=lineage,lineage_sha256=row['fingerprint'])
                if live!=expected:raise ValueError('upstream source differs from retained review snapshot')
            def current():
                compare_source()
                receipt=read_validated_staged_receipt(self,capability,artifacts_base=artifacts_base,
                    lineage=lineage,lineage_sha256=row['fingerprint'],manifest_sha256=manifest_sha256,
                    options=held['options'])
                compare_source()
                return receipt
            evidence=current()
            return self.append_closed_revision_preparation_event(task_id,lineage_id=lineage_id,
                expected_previous_sha256=expected_previous_sha256,state='staged',evidence=evidence,
                read_staged_receipt=current)

    def retain_revision_source_review(self,task_id,*,lineage_id,artifacts_base):
        """Freeze exact upstream bytes for later review, not approve or install.

        Caller owns same-host source writer exclusion. artifacts_base is server
        configuration, never a browser-supplied path.
        """
        from .worker_capability import WorkerCapability
        from .revision_source_review import read_source_review_snapshot
        with self._task_operation_guard('task.rerun'):
            if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            row=self._db.execute('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=? AND task_id=?',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            def current():
                return read_source_review_snapshot(self,capability,artifacts_base=artifacts_base,
                    lineage=json.loads(row['payload']),lineage_sha256=row['fingerprint'])
            snapshot=current()
            from .revision_source_review import source_review_payload
            body=snapshot['snapshot'];planned=json.loads(row['payload'])
            expected=source_review_payload(lineage_sha256=row['fingerprint'],checkpoint_id=planned['source_checkpoint_id'],
                semantic_session_id=planned['semantic_session_id'],phase=planned['phase'],upstream_sha256=body['upstream_sha256'])
            if snapshot!=expected:raise ValueError('source review snapshot differs from held lineage')
            if current()!=snapshot:raise ValueError('source review evidence changed')
            prior=self._db.execute('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=?',(lineage_id,)).fetchone()
            if prior:
                if prior['fingerprint']!=snapshot['snapshot_sha256']:raise ValueError('source review snapshot changed; explicit new review required')
                return {'review_id':prior['review_id'],'snapshot_sha256':prior['fingerprint'],'state':'awaiting_review','accepted':False,'replayed':True}
            scope=current_scope();review_id=secrets.token_hex(16)
            self._db.execute('INSERT INTO closed_revision_source_reviews(review_id,lineage_id,owner_id,auth_session_id,fingerprint,payload,created_at) VALUES (?,?,?,?,?,?,?)',
                (review_id,lineage_id,scope.owner_id,scope.session_id,snapshot['snapshot_sha256'],json.dumps(snapshot['snapshot'],sort_keys=True),time.time()))
            self.audit(scope.owner_id,'gate.revision_source_review_retained',task_id,{'lineage_id':lineage_id,'review_id':review_id})
            return {'review_id':review_id,'snapshot_sha256':snapshot['snapshot_sha256'],'state':'awaiting_review','accepted':False,'replayed':False}

    def prepare_rerun(self,task_id,from_phase,policy='archive',actor='local user'):
        # Legacy generation reset leaves the immutable session binding stale.
        # Block internal callers too until a reviewed new-session copy exists.
        raise TenantAccessDenied('cloud rerun generation/session policy not implemented')

    def prepare_gate_dispatch(self,task_id,gate_key,action,*,actor,model=None,comment='',review_revision=None,**decision):
        """Commit the reviewed gate and its exact next generator job together."""
        from .phases import PHASE_BY_KEY,successor
        if not self._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone():
            raise KeyError(task_id)
        task=self.task(task_id)
        pending=next((g for g in task['gates'] if g['gate_key']==gate_key),None)
        if not pending:raise KeyError(gate_key)
        phase=pending['after_phase']
        target=PHASE_BY_KEY[phase] if action=='redo' else successor(phase)
        if target and target.executor!='generator':
            raise ValueError('cloud gate handoff requires scoped generator execution')
        if target:
            if not model:
                from .model_routes import task_default_model
                model,_=task_default_model(self,task_id,target.generator_stage)
            from .model_routes import route_value
            route,_,name=model.partition(':');route_value(route,name)
        gate=self.decide_gate(task_id,gate_key,action,actor=actor,**decision)
        if comment.strip():self.add_review_comment(task_id,'gate:'+gate_key,review_revision or str(decision.get('expected_revision')),actor,comment)
        if not target:return gate,None,None
        self.set_model_default(task_id,model)
        self.add_event(task_id,'model_route',f'{target.key} gate uses {model}',phase_key=target.key,
          payload={'resolved':model,'scope':'phase_override','actor':actor})
        job=self.enqueue_worker(task_id,'phase:'+target.key,execution_options={
            'mode':'redo' if action=='redo' else 'start','model_route':model})
        return gate,job,target.key

    def recoverable_worker_jobs(self):
        # Authenticated owner view only. Startup never calls this without scope.
        return [dict(row) for row in self._db.execute("SELECT * FROM worker_jobs WHERE state IN ('pending','running','paused','failed') ORDER BY updated_at,job_id")]

    def task_worker_state(self,task_id,limit=20):
        """Read-only worker_jobs + provider_attempts state for one task's diagnostics.

        The Stop-settle gates and a parked interview both live in these two tables, and
        neither was visible in any export: a session parked in stop-requested left its
        job 'running' forever with no way to see that short of a database console. This
        is the same authenticated owner view as recoverable_worker_jobs, scoped to one
        task, newest first, with only the columns a diagnosis needs (no execution
        options, quotes, fingerprints or usage payloads)."""
        jobs=[dict(row) for row in self._db.execute(
            "SELECT job_id,kind,state,attempt,session_id,generation,created_at,updated_at FROM worker_jobs WHERE task_id=? ORDER BY updated_at DESC LIMIT ?",(task_id,limit)).fetchall()]
        attempts=[dict(row) for row in self._db.execute(
            "SELECT p.attempt_id,p.job_id,p.state,p.expense_reserved,p.retail_reserved,p.expense_actual,p.retail_actual,p.created_at,p.updated_at "
            "FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=? ORDER BY p.updated_at DESC LIMIT ?",(task_id,limit)).fetchall()]
        return {'worker_jobs':jobs,'provider_attempts':attempts}

    def worker_job(self,job_id):
        row=self._db.execute('SELECT * FROM worker_jobs WHERE job_id=?',(job_id,)).fetchone()
        return dict(row) if row else None

    def claim_worker(self,job_id):
        from .worker_capability import WorkerCapability
        job=self.worker_job(job_id)
        if not job:return None
        capability=WorkerCapability.capture(self,job['task_id'])
        capability.validate(self)
        if (job['generation']!=capability.generation or job['session_id']!=capability.session_id
            or str(job['auth_session_id'])!=capability.scope.session_id):
            raise TenantAccessDenied('worker job authority changed')
        changed=self._db.execute("UPDATE worker_jobs SET state='running',attempt=attempt+1,updated_at=? WHERE job_id=? AND state IN ('pending','paused')",
                                (time.time(),job_id))
        return capability if changed.rowcount==1 else None

    def pause_interrupted_worker(self,job_id):
        # A live owner explicitly resumes this durable job; no global scan or
        # service-role worker guesses which user's authority to restore.
        self._db.execute("UPDATE worker_jobs SET state='paused',updated_at=? WHERE job_id=? AND state='running'",(time.time(),job_id))

    def reauthorize_worker(self,job_id):
        from .worker_capability import WorkerCapability
        # Serialize owner resume against worker claims and competing resumes.
        self._db.execute('SELECT job_id FROM worker_jobs WHERE job_id=? FOR UPDATE',(job_id,)).fetchone()
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        capability=WorkerCapability.capture(self,job['task_id'])
        capability.validate(self)
        if job['state'] not in ('pending','paused','failed'):
            raise ValueError('only queued/interrupted/failed work can be explicitly resumed')
        if job['generation']!=capability.generation or job['session_id']!=capability.session_id:
            raise TenantAccessDenied('cannot resume another generation/session')
        self._db.execute("UPDATE worker_jobs SET auth_session_id=?,state='paused',updated_at=? WHERE job_id=? AND state IN ('pending','paused','failed')",
                         (capability.scope.session_id,time.time(),job_id))

    def finish_stopped_worker(self,job_id,*,worker_quiescent):
        from .attempt_ledger import AdmissionDenied
        if worker_quiescent is not True:raise AdmissionDenied('worker quiescence unverified')
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        task=self.task(job['task_id'])
        if not task or task['stop_requested_at'] is None or str(job['auth_session_id'])!=current_scope().session_id:
            raise AdmissionDenied('original owner stop authority required')
        self._db.execute("UPDATE worker_jobs SET state='done',updated_at=? WHERE job_id=? AND state='running'",(time.time(),job_id))

    def finish_worker(self,job_id,failed=False):
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        if job['state']=='done':return
        from .worker_capability import WorkerCapability
        WorkerCapability.capture(self,job['task_id']).validate(self)
        self._db.execute("UPDATE worker_jobs SET state=?,updated_at=? WHERE job_id=? AND state='running'",
                         ('failed' if failed else 'done',time.time(),job_id))

    @_transactional
    def staged_response_ledger_join(self,session_id,generation):
        """PR B reconciliation read: one session generation's engine-staged
        provider calls LEFT JOINed to their ledger attempts, in logical-call
        order. Read-only; the gateway classifies the rows."""
        rows=self._db.execute('SELECT r.*, a.state AS attempt_state, a.usage AS attempt_usage '
                              'FROM staged_provider_responses r LEFT JOIN provider_attempts a ON a.attempt_id=r.attempt_id '
                              'WHERE r.session_id=? AND r.generation=? '
                              'ORDER BY r.execution_point,r.node,r.stage,r.item,r.ordinal',(session_id,generation)).fetchall()
        return [dict(r) for r in rows]

    def provider_attempt(self,attempt_id):
        row=self._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
        return dict(row) if row else None

    def reserve_provider_attempt(self,job_id,attempt_id,quote,request_digest,*,require_prepaid=False):
        from .attempt_ledger import reserve
        return reserve(self,job_id,attempt_id,quote,request_digest,require_prepaid=require_prepaid)

    def enqueue_expense_alert(self,alert,*,max_attempts):
        from .attempt_ledger import AdmissionDenied,_integer
        from .expense_alert import ExpenseAlert
        import json,time
        if not isinstance(alert,ExpenseAlert) or not _integer(max_attempts) or not max_attempts:
            raise AdmissionDenied('prepared alert and bounded delivery policy required')
        now=time.time()
        self._db.execute("INSERT INTO expense_alert_outbox VALUES (?,?,?::jsonb,'pending',0,?,?,?) ON CONFLICT(alert_key) DO NOTHING",
            (alert.key,current_scope().owner_id,json.dumps(alert.payload),max_attempts,now,now))
        row=self._db.execute('SELECT * FROM expense_alert_outbox WHERE alert_key=?',(alert.key,)).fetchone()
        if not row:raise AdmissionDenied('alert key unavailable for this owner')
        # Episode payload is first-occurrence evidence, never overwritten by retry.
        return dict(row)

    def claim_expense_alert(self,alert_key):
        row=self._db.execute('SELECT * FROM expense_alert_outbox WHERE alert_key=? FOR UPDATE',(alert_key,)).fetchone()
        if not row:raise KeyError(alert_key)
        if row['state'] not in ('pending','retry_pending'):return None
        self._db.execute("UPDATE expense_alert_outbox SET state='sending',attempts=attempts+1,updated_at=? WHERE alert_key=?",(time.time(),alert_key))
        return dict(self._db.execute('SELECT * FROM expense_alert_outbox WHERE alert_key=?',(alert_key,)).fetchone())

    def finish_expense_alert(self,alert_key,outcome):
        from .expense_alert import delivery_outcome
        from .attempt_ledger import AdmissionDenied
        row=self._db.execute('SELECT * FROM expense_alert_outbox WHERE alert_key=? FOR UPDATE',(alert_key,)).fetchone()
        if not row:raise KeyError(alert_key)
        if row['state']!='sending':raise AdmissionDenied('claimed alert required')
        state=delivery_outcome(outcome=outcome,attempts=row['attempts'],max_attempts=row['max_attempts'])
        self._db.execute('UPDATE expense_alert_outbox SET state=?,updated_at=? WHERE alert_key=?',(state,time.time(),alert_key))
        return state

    def recover_uncertain_expense_alert(self,alert_key):
        # A crash after SMTP acceptance may have sent the email. No blind retry.
        self._db.execute("UPDATE expense_alert_outbox SET state='operator_review',updated_at=? WHERE alert_key=? AND state='sending'",(time.time(),alert_key))

    def record_raw_provider_usage(self,attempt_id,raw_usage):
        from .attempt_ledger import AdmissionDenied
        import json,time
        if not isinstance(raw_usage,dict) or len(json.dumps(raw_usage,allow_nan=False).encode())>65536:
            raise AdmissionDenied('bounded raw provider usage object required')
        row=self._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=? FOR UPDATE',(attempt_id,)).fetchone()
        if not row or row['state'] not in ('dispatched','uncertain','settled'):
            raise AdmissionDenied('dispatched owner attempt required for raw evidence')
        if row['raw_usage'] is not None:
            if row['raw_usage']!=raw_usage:raise AdmissionDenied('conflicting raw usage replay')
            return dict(row)
        self._db.execute('UPDATE provider_attempts SET raw_usage=?::jsonb,raw_usage_received_at=? WHERE attempt_id=?',
            (json.dumps(raw_usage,allow_nan=False),time.time(),attempt_id))
        return self.provider_attempt(attempt_id)

    def confirmed_expense_admission(self,job_id,expense_scale=None):
        # Returning a denial commits its durable episode/outbox. Raising here
        # would roll it back and lose the alert on every denied call.
        row=self._db.execute('SELECT public.requi_confirmed_expense_guard(?,?) AS reason',(job_id,expense_scale)).fetchone()
        return row['reason']

    def start_launch_provider_attempt(self,attempt_id):
        from .launch_admission_policy import validate_owner_limits,validate_launch_quote
        from .attempt_ledger import AdmissionDenied
        self._db.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
        row=self.provider_attempt(attempt_id)
        if not row:raise AdmissionDenied('owned attempt required')
        limits=self._db.execute('SELECT * FROM admission_limits WHERE owner_id=?',(current_scope().owner_id,)).fetchone()
        validate_owner_limits(dict(limits) if limits else None);validate_launch_quote(row['quote'])
        reason=self.confirmed_expense_admission(row['job_id'],row['quote']['expense_units_per_usd'])
        if reason!='allowed':return {'started':False,'reason':reason}
        self.transition_provider_attempt(attempt_id,'start',require_prepaid=True)
        return {'started':True,'reason':'allowed'}

    def transition_provider_attempt(self,attempt_id,action,usage=None,*,require_prepaid=False):
        from .attempt_ledger import transition
        return transition(self,attempt_id,action,usage,require_prepaid=require_prepaid)

    def record_provider_balance_check(self,attempt_id,evidence):
        from .provider_balance import validate_balance
        row=self.provider_attempt(attempt_id)
        if not row or row['state']!='reserved':raise ValueError('reserved owner attempt required for balance evidence')
        checked=validate_balance(evidence,row['expense_reserved'],row['quote']['expense_units_per_usd'])
        self._db.execute('UPDATE provider_attempts SET provider_balance_evidence=?::jsonb WHERE attempt_id=?',(json.dumps(checked),attempt_id))
        return checked

    def task_retail_totals(self,task_id):
        from .retail_ledger import task_totals
        return task_totals(self,task_id)

    def task_phase_credits(self,task_id):
        from .retail_ledger import task_phase_totals
        return task_phase_totals(self,task_id)

    def retail_compute_status(self,task_id):
        rows=self._db.execute("SELECT r.state,count(*) AS count FROM retail_compute_segments r JOIN worker_jobs w ON r.job_id=w.job_id WHERE w.task_id=? GROUP BY r.state",(task_id,)).fetchall()
        return {r['state']:r['count'] for r in rows}

    def begin_retail_compute(self,key,job_id,phase,rates):
        from .retail_ledger import begin_compute
        return begin_compute(self,key,job_id,phase,rates)

    def close_failed_retail_compute(self,key):
        from .retail_ledger import close_failed_compute
        return close_failed_compute(self,key)

    def release_own_compute(self,confirm):
        from .retail_ledger import release_compute
        return release_compute(self,current_scope().owner_id,bool(confirm))

    def finish_retail_compute(self,key,deterministic_ms):
        from .retail_ledger import finish_compute
        return finish_compute(self,key,deterministic_ms)

    def post_retail_contribution(self,**contribution):
        from .retail_ledger import post
        return post(self,**contribution)

    def retail_phase_totals(self,job_id):
        from .retail_ledger import phase_totals
        return phase_totals(self,job_id)

    def operator_workspace_snapshot(self):
        """Read-only current-owner operations, never cross-tenant privilege."""
        owner=current_scope().owner_id
        account=self._db.execute('SELECT balance_units,units_per_credit,updated_at FROM credit_accounts WHERE owner_id=?',(owner,)).fetchone()
        sessions=self._db.execute('SELECT session_id,task_id,generation FROM engine_sessions WHERE owner_id=? ORDER BY session_id',(owner,)).fetchall()
        jobs=self._db.execute('SELECT job_id,task_id,session_id,generation,kind,state FROM worker_jobs WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100',(owner,)).fetchall()
        return {'scope':'current_authorized_workspace','owner_id':owner,
            'credits':dict(account) if account else None,'sessions':[dict(row) for row in sessions],
            'worker_jobs':[dict(row) for row in jobs],'credit_adjustment_available':False,
            'global_operator_access':False}

    def own_credit_balance(self,usd_per_credit):
        """Read-only: the signed-in owner's own prepaid balance. Public, so it runs inside a scoped transaction like every store operation."""
        from .account_balance import read
        return read(self._db,current_scope().owner_id,usd_per_credit)

    def own_credit_history(self):
        """Read-only: every recorded change to the signed-in owner's credit balance."""
        from . import credit_history
        return credit_history.read(self._db)

    def own_last_topup(self):
        """Read-only: the latest top-up or grant to the signed-in owner, or None."""
        from . import credit_history
        return credit_history.last_topup(self._db)

    def own_ledger(self,usd_per_credit):
        """Read-only: the signed-in owner's credit balance and provider-call ledger, as text for the Debug dialog."""
        from .account_ledger import read
        return read(self._db,current_scope().owner_id,usd_per_credit)

    def _unsupported(self,*args,**kwargs):
        raise TenantAccessDenied('local authentication or quota provisioning unavailable on user connection')


# User-entry mutations and new work claims only. Nested accepted-worker writes,
# progress/usage evidence and completion remain available. Never gate settlement.
    # ---- PR B: artifact manifest authority (Postgres-only) ----
    def publish_task_artifact(self,task_id,phase_key,name,ref,*,mime_type='application/json',writer_instance,validation_report=None):
        """Measure, store and atomically publish one artifact: bytes into tenant-owned
        artifact_bytes (content-addressed, immutable), a staged manifest row, then one
        CAS demote-and-promote. The whole publication is this operation's transaction.

        validation_report binds a validator verdict to the EXACT bytes being stored:
        {'validator':name,'valid':bool,'error':str|None,'sha256':hex,
        'validator_version':str}. Its sha256 must equal the measured hash of the
        published bytes, otherwise the verdict came from other content and the publish
        is refused - an unvalidated artifact can never carry a verdict it did not earn.
        validator_version is the provenance of the validation code itself (package
        version or schema fingerprint from validator_provenance); 'unknown' must be
        explicit. A verdict recorded under one version is never reinterpreted as
        another."""
        import hashlib
        from pathlib import Path
        data=Path(ref).read_bytes()
        digest=hashlib.sha256(data).hexdigest();size=len(data)
        validator=valid=validation_error=validator_version=None
        if validation_report is not None:
            if validation_report.get('sha256')!=digest:
                raise ValueError('validation report hash does not match the published bytes')
            if 'validator_version' not in validation_report:
                raise ValueError('validation report must name its validator_version provenance')
            validator=validation_report.get('validator');valid=validation_report.get('valid')
            validation_error=validation_report.get('error');validator_version=validation_report['validator_version']
        task=self.task(task_id)
        if not task: raise KeyError(task_id)
        scope=current_scope();now=time.time()
        self._db.execute('INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES (?,?,?,?) ON CONFLICT (owner_id,sha256) DO NOTHING',
            (scope.owner_id,digest,data,size))
        existing=self._db.execute('SELECT bytes FROM artifact_bytes WHERE owner_id=? AND sha256=?',(scope.owner_id,digest)).fetchone()
        if existing is None or bytes(existing['bytes'])!=data:
            raise ValueError('stored artifact bytes do not match the recorded content hash')
        self._db.execute(
            "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at,byte_size,sha256,publication_state,writer_instance,validator,valid,validation_error,validator_version) VALUES (?,?,?,?,?,?,?,?,?,'staged',?,?,?,?,?)",
            (task_id,task['generation'],phase_key,name,str(ref),mime_type,now,size,digest,writer_instance,validator,valid,validation_error,validator_version))
        self._db.execute("UPDATE task_artifacts SET publication_state='superseded' WHERE task_id=? AND generation=? AND phase_key=? AND name=? AND publication_state='published'",
            (task_id,task['generation'],phase_key,name))
        changed=self._db.execute("UPDATE task_artifacts SET publication_state='published',published_at=? WHERE task_id=? AND generation=? AND phase_key=? AND name=? AND publication_state='staged' AND sha256=?",
            (now,task_id,task['generation'],phase_key,name,digest))
        if changed.rowcount!=1: raise ValueError('artifact publication CAS failed')
        return dict(self._db.execute("SELECT * FROM task_artifacts WHERE task_id=? AND generation=? AND phase_key=? AND name=? AND publication_state='published'",
            (task_id,task['generation'],phase_key,name)).fetchone())

    def manifest_artifacts(self,task_id,*,generation=None):
        """Manifest rows for a task (all publication states), newest generation first."""
        task=self.task(task_id)
        if not task: return []  # foreign or missing task: no existence oracle
        if generation is None: generation=task['generation']
        return [dict(r) for r in self._db.execute(
            'SELECT * FROM task_artifacts WHERE task_id=? AND generation=? ORDER BY phase_key,name,id',
            (task_id,generation)).fetchall()]

    def manifest_ready(self,task_id,generation=None):
        """True when a complete published revision is marked ready for this
        task+generation AND the marker still matches the published set (a stale or
        corrupt marker raises instead of vouching for a revision it no longer names)."""
        return self.manifest_revision(task_id,generation) is not None

    @staticmethod
    def _revision_hash(rows):
        """sha256 over the sorted published (phase_key,name,sha256) members - the exact
        manifest revision a readiness marker is bound to."""
        import hashlib
        body='\n'.join(f"{r['phase_key']}/{r['name']}:{r['sha256']}" for r in sorted(rows,key=lambda r:(r['phase_key'],r['name'])))
        return hashlib.sha256(body.encode()).hexdigest()

    def _mark_revision_ready(self,task,revision_id,revision_hash,expected_revision_id,writer_instance,now):
        """Compare-and-swap the readiness marker from the expected prior revision to the
        new one. A concurrent producer that already re-marked makes this match zero rows;
        the raise rolls this whole revision's publication back, so readers never see a
        manifest mixing two producers' members."""
        scope=current_scope()
        if expected_revision_id is None:
            changed=self._db.execute(
                'INSERT INTO artifact_manifest_ready(owner_id,task_id,generation,revision_id,revision_hash,marked_at,writer_instance) VALUES (?,?,?,?,?,?,?) ON CONFLICT (task_id,generation) DO NOTHING',
                (scope.owner_id,task['task_id'],task['generation'],revision_id,revision_hash,now,writer_instance))
        else:
            changed=self._db.execute(
                'UPDATE artifact_manifest_ready SET revision_id=?,revision_hash=?,marked_at=?,writer_instance=? WHERE task_id=? AND generation=? AND revision_id=?',
                (revision_id,revision_hash,now,writer_instance,task['task_id'],task['generation'],expected_revision_id))
        if changed.rowcount!=1: raise ValueError('readiness CAS failed: a concurrent producer published a revision')

    def publish_task_revision(self,task_id,artifacts,*,writer_instance):
        """Publish one COMPLETE validated revision atomically, in this operation's
        single transaction: every member's bytes + staged row + CAS promote, supersede
        published members of this generation that the new revision omits, then CAS the
        readiness marker (bound to the exact revision hash) from the expected prior
        revision. Any failure - including losing the race to a concurrent producer -
        rolls everything back: no partial authority, no mixed revisions.
        artifacts: [{'phase_key','name','ref','mime_type'?,'validation_report'?}]."""
        import uuid as _uuid
        task=self.task(task_id)
        if not task: raise KeyError(task_id)
        marker=self._db.execute('SELECT revision_id FROM artifact_manifest_ready WHERE task_id=? AND generation=?',
            (task_id,task['generation'])).fetchone()
        expected=marker['revision_id'] if marker else None
        revision_id=_uuid.uuid4().hex
        published=[self.publish_task_artifact(task_id,a['phase_key'],a['name'],a['ref'],
            mime_type=a.get('mime_type','application/json'),writer_instance=writer_instance,
            validation_report=a.get('validation_report')) for a in artifacts]
        keep={(a['phase_key'],a['name']) for a in artifacts}
        for row in self._db.execute("SELECT phase_key,name FROM task_artifacts WHERE task_id=? AND generation=? AND publication_state='published'",
            (task_id,task['generation'])).fetchall():
            if (row['phase_key'],row['name']) not in keep:
                self._db.execute("UPDATE task_artifacts SET publication_state='superseded' WHERE task_id=? AND generation=? AND phase_key=? AND name=? AND publication_state='published'",
                    (task_id,task['generation'],row['phase_key'],row['name']))
        members=self._db.execute("SELECT phase_key,name,sha256 FROM task_artifacts WHERE task_id=? AND generation=? AND publication_state='published'",
            (task_id,task['generation'])).fetchall()
        self._mark_revision_ready(task,revision_id,self._revision_hash(members),expected,writer_instance,time.time())
        return published

    def manifest_snapshot(self,task_id,generation=None):
        """Marker + published members read as ONE statement = one consistent database
        snapshot: no check/read race with a concurrent publisher. Returns
        {'revision_id','revision_hash','rows'} or None when no ready marker exists.
        A marker whose hash no longer matches the members in the same snapshot is
        corruption - raise, never serve against it."""
        import json as _json
        row=self._db.execute(
            "WITH m AS (SELECT revision_id,revision_hash FROM artifact_manifest_ready WHERE task_id=? AND generation=coalesce(?,(SELECT generation FROM tasks WHERE task_id=?))) "
            "SELECT m.revision_id,m.revision_hash,"
            "(SELECT json_agg(row_to_json(t)) FROM (SELECT * FROM task_artifacts WHERE task_id=? AND generation=coalesce(?,(SELECT generation FROM tasks WHERE task_id=?)) AND publication_state='published' ORDER BY phase_key,name,id) t) AS members FROM m",
            (task_id,generation,task_id,task_id,generation,task_id)).fetchone()
        if row is None:return None
        members=row['members']
        if isinstance(members,str):members=_json.loads(members)
        rows=[dict(r) for r in (members or [])]
        if self._revision_hash(rows)!=row['revision_hash']:
            raise ValueError('readiness marker does not match the published manifest revision')
        return {'revision_id':row['revision_id'],'revision_hash':row['revision_hash'],'rows':rows}

    def manifest_revision(self,task_id,generation=None):
        """The ready marker for this task+generation, verified against the current
        published set: (revision_id, revision_hash) or None when not ready. A marker
        whose hash no longer matches the published rows is corruption - raise, never
        serve against it."""
        task=self.task(task_id)
        if not task:return None
        if generation is None:generation=task['generation']
        marker=self._db.execute('SELECT revision_id,revision_hash FROM artifact_manifest_ready WHERE task_id=? AND generation=?',
            (task_id,generation)).fetchone()
        if marker is None:return None
        members=self._db.execute("SELECT phase_key,name,sha256 FROM task_artifacts WHERE task_id=? AND generation=? AND publication_state='published'",
            (task_id,generation)).fetchall()
        if self._revision_hash(members)!=marker['revision_hash']:
            raise ValueError('readiness marker does not match the published manifest revision')
        return marker['revision_id'],marker['revision_hash']

    def producer_result(self,job_id):
        """Durable produced-awaiting-publication state for a worker job, or None."""
        row=self._db.execute('SELECT * FROM producer_results WHERE job_id=?',(job_id,)).fetchone()
        return dict(row) if row else None

    def record_producer_result(self,job_id,task_id,generation,session_root,content_hash):
        """Persist that the paid producer completed: its bytes are at session_root
        (pinned by content_hash) and only publication remains."""
        import time as _time
        self._db.execute(
            'INSERT INTO producer_results(owner_id,job_id,task_id,generation,session_root,content_hash,produced_at) VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT (job_id) DO UPDATE SET task_id=EXCLUDED.task_id,generation=EXCLUDED.generation,'
            'session_root=EXCLUDED.session_root,content_hash=EXCLUDED.content_hash,produced_at=EXCLUDED.produced_at,published_at=NULL',
            (current_scope().owner_id,job_id,task_id,generation,session_root,content_hash,_time.time()))

    def producer_paid_evidence(self,job_id):
        """Count of provider attempt rows recorded for a job: evidence that paid work
        may already have happened when no produced marker exists."""
        row=self._db.execute('SELECT count(*) AS n FROM provider_attempts WHERE job_id=?',(job_id,)).fetchone()
        return row['n'] if row else 0

    def mark_producer_published(self,job_id):
        import time as _time
        changed=self._db.execute('UPDATE producer_results SET published_at=? WHERE job_id=? AND published_at IS NULL',
            (_time.time(),job_id))
        if changed.rowcount!=1: raise ValueError('producer result was already marked published')

    def read_artifact_bytes_sha(self,sha256):
        """Read immutable bytes by content hash - one exact published revision, safe to
        combine with a previously taken manifest snapshot under concurrent republish."""
        row=self._db.execute('SELECT bytes FROM artifact_bytes WHERE owner_id=? AND sha256=?',(current_scope().owner_id,sha256)).fetchone()
        if row is None: raise KeyError(f'no stored bytes for hash {sha256[:12]}')
        data=bytes(row['bytes'])
        import hashlib
        if hashlib.sha256(data).hexdigest()!=sha256: raise ValueError('stored bytes failed hash verification')
        return data

    def read_artifact_bytes(self,task_id,phase_key,name,*,generation=None):
        """Hash-verified artifact bytes via the manifest only. Raises on a missing
        manifest row, missing bytes row or hash mismatch - never a filesystem read."""
        import hashlib
        task=self.task(task_id)
        if not task: raise KeyError(f'no published artifact {phase_key}/{name}')
        if generation is None: generation=task['generation']
        row=self._db.execute("SELECT * FROM task_artifacts WHERE task_id=? AND generation=? AND phase_key=? AND name=? AND publication_state='published'",
            (task_id,generation,phase_key,name)).fetchone()
        if row is None: raise KeyError(f'no published artifact {phase_key}/{name}')
        stored=self._db.execute('SELECT bytes FROM artifact_bytes WHERE owner_id=? AND sha256=?',
            (current_scope().owner_id,row['sha256'])).fetchone()
        if stored is None: raise ValueError(f'manifest row without stored bytes for {phase_key}/{name}')
        data=bytes(stored['bytes'])
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError(f'stored bytes failed hash verification for {phase_key}/{name}')
        return data

_TASK_ENTRY_OPERATIONS={
 'create_task':'task.create','rename_task':'task.edit','delete_task':'task.delete',
 'tombstone_stopped_task':'task.delete','request_task_stop':'task.stop',
 'configure_simulation':'task.simulate','set_gate_open':'task.gate.decide',
 'decide_gate':'task.gate.decide','prepare_gate_dispatch':'task.gate.decide',
 'add_review_comment':'task.decision.comment','save_decision_draft':'task.decision.draft',
 'set_model_route':'task.model_route','set_model_default':'task.model_route',
 'begin_interview_start':'task.start','enqueue_worker':'task.start',
 'claim_worker':'worker.claim','reauthorize_worker':'worker.claim',
 'prepare_phase_retry':'task.retry','claim_failed_needs_retry':'task.retry',
 'claim_failed_phase_retry':'task.retry','prepare_rerun':'task.rerun',
 'upsert_feedback':'task.edit','update_feedback':'task.edit','delete_feedback':'task.edit'}

# Only explicit public operations wrap, not helpers. A newly added inherited
# method also gets a transaction by default; no unsafe unscoped SQL pathway.
_LOCAL_AUTH = {'create_oauth_state','consume_oauth_state','upsert_user','create_basic_user',
 'basic_user','create_session','session','rotate_session','delete_session','set_user_quota'}
for _name in set(vars(SQLiteStore)) | set(vars(PostgresStore)):
    if _name.startswith('_'): continue
    _method=getattr(PostgresStore,_name,None)
    if callable(_method):
        setattr(PostgresStore,_name,_transactional(PostgresStore._unsupported if _name in _LOCAL_AUTH else _method))
