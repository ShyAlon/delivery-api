"""Postgres control plane sharing the existing relational state machine.

Shared operations use the PostgreSQL transaction and tenant-claims bridge.
Every public operation gets one scoped Postgres transaction. Nested operations
reuse that transaction, and commit/rollback is owned only by its outer boundary.
Local authentication/token tables have no place in this adapter.
"""
from __future__ import annotations
from contextvars import ContextVar
from functools import wraps
import json
import secrets
import time
import contextlib
from types import SimpleNamespace
from .phases import PHASE_DEFINITIONS

PHASES = tuple((phase.key, phase.label) for phase in PHASE_DEFINITIONS)
from . import event_bus
from .phases import followers, predecessors, PHASE_POSITIONS
from vmodel_contracts import RunState
from .postgres_scope import ScopedPostgres, note_query, note_statement, statement_label
from .tenancy import current_scope, TenantAccessDenied


def _transactional(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        if method.__name__=='task_for_engine_session' and not self._map_dirty.get() and len(args)==1 and not kwargs:
            from . import session_task_cache
            hit=session_task_cache.get(self,args[0])    # a hit needs no transaction at all
            if hit is not None:return hit
        if self._connection.get() is not None:
            return method(self,*args,**kwargs)
        with contextlib.ExitStack() as cleanup:
            if method.__name__ in ('ensure_waiting_input_event','retain_decision_review','ingest_interview_progress_page','admit_provider_attempt'):
                # Observations made during GET must commit before the read
                # continues, never pin task/owner locks until HTTP teardown.
                from .postgres_scope import _request_tx
                request_token=_request_tx.set(None)
                cleanup.callback(_request_tx.reset,request_token)
            dirty_token=self._cache_dirty.set(False)
            def invalidate_after_transaction():
                dirty=self._cache_dirty.get()
                self._cache_dirty.reset(dirty_token)
                if dirty:
                    from . import task_read_cache
                    task_read_cache.invalidate(self)
            cleanup.callback(invalidate_after_transaction)
            map_token=self._map_dirty.set(False)
            def invalidate_mapping_after_transaction():
                dirty=self._map_dirty.get()
                self._map_dirty.reset(map_token)
                if dirty:
                    from . import session_task_cache
                    session_task_cache.invalidate(self)     # commit or rollback: either way drop what this transaction touched
            cleanup.callback(invalidate_mapping_after_transaction)
            connection=cleanup.enter_context(self._postgres.transaction(savepoint=True))
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


class PostgresStore:
    def __init__(self,dsn):
        self._postgres = ScopedPostgres(dsn)
        self._connection = ContextVar('postgres_operation',default=None)
        self._cache_dirty = ContextVar('postgres_cache_dirty',default=False)
        self._map_dirty = ContextVar('postgres_session_map_dirty',default=False)

    def ensure_waiting_input_event(self,task_id:str,message:str,*,phase_key:str,payload:dict)->int:
        # Lock before checking. Under READ COMMITTED, a waiter then sees the
        # winner's committed event. This is a database row lock, never a Python
        # mutex held across a request's transaction or another PG lock wait.
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
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
            with self._postgres.transaction(savepoint=True) as connection:
                token=self._connection.set(connection)
                try:
                    owner_lock(self);require_task_operation(self,action)
                    yield
                finally:self._connection.reset(token)
        return guard()

    def authority_snapshot(self,task_id,session_id):
        """Task record, session binding and auth-session liveness from ONE statement, one snapshot.

        Replaces three separate reads (each its own pool check, identity statement and commit on a
        pooled link). Returns (task or None, binding or None, active)."""
        sql=self._TASK_PROJECTION.replace("\nFROM tasks t",
            ",\n  (SELECT to_jsonb(e)::text FROM engine_sessions e WHERE e.session_id=%s) AS binding_json,"
            "\n  public.requi_session_active() AS session_active\nFROM tasks t",1)
        row=self._execute_native(sql+' WHERE t.task_id=%s AND t.deleted_at IS NULL',(session_id,task_id)).fetchone()
        if not row:
            # No task row (deleted or other tenant): still report liveness, one cheap statement on the denial path only.
            return None,None,self.auth_session_active()
        row=dict(row);binding_json=row.pop('binding_json');active=bool(row.pop('session_active'))
        task=self._task_record(row)
        binding=json.loads(binding_json) if binding_json else None
        if binding:binding['owner_id']=str(binding['owner_id'])
        return task,binding,active

    def auth_session_active(self):
        return bool(self._execute_native('SELECT public.requi_session_active() AS active').fetchone()['active'])

    def record_failure_diagnostics(self, task_id: str, source: str, http_status: int, block: dict) -> int:
        """Tenant-scoped operator-only failure evidence on Postgres: the row
        carries the caller's verified owner and the task's generation, and the
        write policy checks owner_id against the session claim."""
        block = self._validated_diagnostics_block(block)
        owner = current_scope().owner_id
        task = self._execute_native('SELECT generation FROM tasks WHERE task_id=%s AND deleted_at IS NULL',(task_id,)).fetchone()
        if not task:
            raise KeyError('task not found')
        row = self._execute_native(
            "INSERT INTO operator_failure_diagnostics(owner_id,task_id,generation,source,http_status,error_class,sqlstate,stage,role,"
            "sub_present,session_id_present,session_active_for_sub,message,block,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (owner, task_id, task['generation'], source, http_status, block['error_class'], block.get('sqlstate'),
             block.get('stage'), block['role'], str(block['sub_present']), str(block['session_id_present']),
             str(block['session_active_for_sub']), block['message'], json.dumps(block), time.time()), write=True)
        return row.fetchone()['id']

    def add_event(self, task_id: str, kind: str, message: str, *, phase_key: str | None=None, payload: dict | None=None) -> int:
        # One statement: the event row and the task's updated_at touch share a round trip (was INSERT then UPDATE).
        now = time.time()
        cur = self._execute_native('WITH ev AS (INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id), '
                                   'touched AS (UPDATE tasks SET updated_at=%s WHERE task_id=%s) SELECT id FROM ev',
                                   (task_id, phase_key, kind, message, json.dumps(payload or {}), now, now, task_id), write=True)
        self._connection.get().notify_after_commit(task_id)
        return cur.fetchone()['id']

    def events(self, task_id: str, after: int=0, limit: int=500) -> list[dict]:
        if after:
            rows = self._execute_native('SELECT * FROM events WHERE task_id=%s AND id>%s ORDER BY id LIMIT %s', (task_id, after, limit)).fetchall()
        else:
            rows = list(reversed(self._execute_native('SELECT * FROM events WHERE task_id=%s ORDER BY id DESC LIMIT %s', (task_id, limit)).fetchall()))
        result = []
        for row in rows:
            item = dict(row)
            item['payload'] = json.loads(item['payload'])
            result.append(item)
        return result

    def progress_cursor(self, task_id: str, source: str, session_id: str) -> int:
        row = self._execute_native('SELECT p.cursor FROM tasks t LEFT JOIN progress_ingest p ON p.task_id=t.task_id AND p.source=%s AND p.session_id=%s WHERE t.task_id=%s AND t.deleted_at IS NULL', (source, session_id, task_id)).fetchone()
        if row is None:raise KeyError(task_id)
        return int(row['cursor']) if row['cursor'] is not None else 0

    def ingest_progress(self, signal, source: str) -> bool:
        """Deduplicate and commit task event + source cursor as one transaction."""
        from vmodel_contracts.progress_signal import ProgressSignal
        signal = ProgressSignal.model_validate(signal)
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (signal.task_id,)).fetchone():
            raise KeyError(signal.task_id)
        row = self._execute_native('SELECT cursor FROM progress_ingest WHERE task_id=%s AND source=%s AND session_id=%s', (signal.task_id, source, signal.session_id)).fetchone()
        cursor = int(row['cursor']) if row else 0
        if source == 'interview' and signal.cursor <= cursor:
            return False
        if source == 'interview' and signal.cursor != cursor + 1:
            raise ValueError('interview progress cursor gap')
        if self._execute_native('SELECT 1 FROM progress_seen WHERE task_id=%s AND source=%s AND event_id=%s', (signal.task_id, source, signal.event_id)).fetchone():
            return False
        message = signal.detail or signal.label
        payload = {**signal.model_dump(mode='json'), 'source': source}
        try:
            self._execute_native('INSERT INTO progress_seen VALUES (%s,%s,%s)', (signal.task_id, source, signal.event_id), write=True)
            self._execute_native('INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s)', (signal.task_id, signal.phase_key, 'progress', message, json.dumps(payload), time.time()), write=True)
            self._execute_native('INSERT INTO progress_ingest(task_id,source,session_id,cursor) VALUES (%s,%s,%s,%s) ON CONFLICT(task_id,source,session_id) DO UPDATE SET cursor=GREATEST(progress_ingest.cursor,excluded.cursor)', (signal.task_id, source, signal.session_id, signal.cursor), write=True)
            self._execute_native("UPDATE phases SET detail=%s,completed=COALESCE(%s,completed),total=COALESCE(%s,total) WHERE task_id=%s AND phase_key=%s AND state='running'", (message, signal.completed, signal.total, signal.task_id, signal.phase_key), write=True)
            self._execute_native('UPDATE tasks SET updated_at=%s WHERE task_id=%s', (time.time(), signal.task_id), write=True)
            self._connection.get().notify_after_commit(signal.task_id)
        except Exception:
            self._connection.get().execute('ROLLBACK TO SAVEPOINT operation_start')
            raise
        return True

    def ingest_interview_progress_page(self, task_id, session_id, page):
        """One atomic ordered page, not one transaction per observational signal.

        Source cursor consumes every signal. Only stage/item history is copied;
        heartbeats and per-call summaries stay in source diagnostics/accounting.
        Nothing here publishes engine control, changes a phase state or charges.
        """
        from .progress_adapter import validate_interview_page, retain_interview_signal
        validate_interview_page(task_id,session_id,page)
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
            raise KeyError(task_id)
        row=self._execute_native('SELECT cursor FROM progress_ingest WHERE task_id=%s AND source=%s AND session_id=%s',
                                 (task_id,'interview',session_id)).fetchone()
        cursor=int(row['cursor']) if row else 0
        if page.next_cursor<cursor:raise ValueError('engine progress cursor moved backwards')
        fresh=[signal for signal in page.events if signal.cursor>cursor]
        if not fresh:
            if page.next_cursor>cursor:raise ValueError('engine returned an advanced cursor with no events')
            return 0
        if fresh[0].cursor!=cursor+1:raise ValueError('interview progress cursor gap')
        kept=[signal for signal in fresh if retain_interview_signal(signal)]
        records=[dict(event_id=signal.event_id,phase_key=signal.phase_key,message=signal.detail or signal.label,
                      payload={**signal.model_dump(mode='json'),'source':'interview'}) for signal in kept]
        # Set-based dedupe/events; only the final retained signal per phase updates
        # its display projection. Cursor and task timestamp update once per page.
        self._execute_native("""WITH incoming AS (
          SELECT value AS item,ordinality AS ord FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY
        ), seen AS (
          INSERT INTO progress_seen(task_id,source,event_id)
          SELECT %s,'interview',item->>'event_id' FROM incoming
          ON CONFLICT DO NOTHING RETURNING event_id
        ), retained AS (
          SELECT i.* FROM incoming i JOIN seen s ON s.event_id=i.item->>'event_id'
        ), emitted AS (
          INSERT INTO events(task_id,phase_key,kind,message,payload,ts)
          SELECT %s,item->>'phase_key','progress',item->>'message',(item->'payload')::text,%s
          FROM retained ORDER BY ord RETURNING id
        ), latest AS (
          SELECT DISTINCT ON (item->>'phase_key') item,
            (SELECT r.item->'payload'->>'completed' FROM retained r
             WHERE r.item->>'phase_key'=x.item->>'phase_key' AND r.item->'payload'->>'completed' IS NOT NULL
             ORDER BY r.ord DESC LIMIT 1) AS completed,
            (SELECT r.item->'payload'->>'total' FROM retained r
             WHERE r.item->>'phase_key'=x.item->>'phase_key' AND r.item->'payload'->>'total' IS NOT NULL
             ORDER BY r.ord DESC LIMIT 1) AS total
          FROM retained x ORDER BY item->>'phase_key',ord DESC
        ), projected AS (
          UPDATE phases p SET detail=l.item->>'message',
            completed=COALESCE(l.completed::int,p.completed),
            total=COALESCE(l.total::int,p.total)
          FROM latest l WHERE p.task_id=%s AND p.phase_key=l.item->>'phase_key' AND p.state='running'
        ), touched AS (
          UPDATE tasks SET updated_at=%s WHERE task_id=%s AND EXISTS(SELECT 1 FROM retained)
        ) INSERT INTO progress_ingest(task_id,source,session_id,cursor) VALUES (%s,'interview',%s,%s)
          ON CONFLICT(task_id,source,session_id) DO UPDATE SET cursor=excluded.cursor""",
          (json.dumps(records),task_id,task_id,time.time(),task_id,time.time(),task_id,task_id,session_id,page.next_cursor),write=True)
        self._connection.get().notify_after_commit(task_id)
        return len(kept)

    def audit(self, actor: str, action: str, task_id: str | None=None, detail: dict | None=None) -> int:
        cur = self._execute_native('INSERT INTO audit_log(actor,action,task_id,detail,ts) VALUES (%s,%s,%s,%s,%s) RETURNING id', (actor, action, task_id, json.dumps(detail or {}), time.time()), write=True)
        return cur.fetchone()['id']

    def audit_entries(self, task_id: str | None=None, limit: int=500) -> list[dict]:
        if task_id:
            rows = self._execute_native('SELECT * FROM audit_log WHERE task_id=%s ORDER BY id DESC LIMIT %s', (task_id, limit))
        else:
            rows = self._execute_native('SELECT * FROM audit_log ORDER BY id DESC LIMIT %s', (limit,))
        result = []
        for row in rows:
            item = dict(row)
            item['detail'] = json.loads(item['detail'])
            result.append(item)
        return result

    def _ensure_waiting_input_locked(self, task_id, message, *, phase_key, payload):
        input_id = payload.get('input_id')
        if not isinstance(input_id, str) or not input_id:
            raise ValueError('waiting input identity required')
        event = self._execute_native("SELECT id FROM events WHERE task_id=%s AND kind='waiting_input' AND payload::jsonb->>'input_id'=%s ORDER BY id LIMIT 1", (task_id,input_id)).fetchone()
        if event:return event['id']
        return self.add_event(task_id, 'waiting_input', message, phase_key=phase_key, payload=payload)

    @staticmethod
    def _validated_diagnostics_block(block: dict) -> dict:
        """The operator block is presence-only by contract: booleans or the
            honest UNKNOWN, never claim values, bounded sanitized message."""
        if not isinstance(block, dict):
            raise ValueError('operator diagnostics block required')
        out = dict(block)
        for key in ('sub_present', 'session_id_present', 'session_active_for_sub'):
            if out.get(key) not in (True, False, 'UNKNOWN'):
                raise ValueError(f'operator diagnostics {key} must be a presence boolean or UNKNOWN')
        out['error_class'] = str(out.get('error_class') or 'UNKNOWN')[:120]
        out['role'] = str(out.get('role') or 'UNKNOWN')[:120]
        out['message'] = str(out.get('message') or '')[:300]
        if out.get('sqlstate') is not None:
            sqlstate = str(out['sqlstate'])
            if len(sqlstate) != 5:
                raise ValueError('operator diagnostics sqlstate is the five-character class code')
            out['sqlstate'] = sqlstate
        if out.get('stage') is not None:
            out['stage'] = str(out['stage'])[:80]
        return out

    def failure_diagnostics(self, task_id: str, limit: int=20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('bounded diagnostics page required')
        rows = self._execute_native('SELECT id,task_id,source,http_status,error_class,sqlstate,stage,role,sub_present,session_id_present,session_active_for_sub,message,block,created_at AS ts FROM operator_failure_diagnostics WHERE task_id=%s ORDER BY id DESC LIMIT %s', (task_id, limit)).fetchall()
        out = []
        for row in rows:
            row = dict(row)
            if isinstance(row['block'],str):
                row['block'] = json.loads(row['block'])
            out.append(row)
        return out

    def add_review_comment(self, task_id: str, wait_id: str, revision: str, actor: str, comment: str) -> int:
        cursor = self._execute_native('INSERT INTO review_comments(task_id,wait_id,revision,actor,comment,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id', (task_id, wait_id, revision, actor, comment, time.time()), write=True)
        return cursor.fetchone()['id']

    def review_comments(self, task_id: str, wait_id: str, revision: str) -> list[dict]:
        rows = self._execute_native('SELECT id,actor,comment,created_at FROM review_comments WHERE task_id=%s AND wait_id=%s AND revision=%s ORDER BY id', (task_id, wait_id, revision)).fetchall()
        return [dict(row) for row in rows]

    def retain_decision_review(self, task_id: str, wait_id: str, revision: str, payload: dict) -> None:
        retained = self._execute_native('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s', (task_id, wait_id, revision)).fetchone()
        if retained is not None:
            if json.loads(retained['payload']) != json.loads(json.dumps(payload)):
                raise ValueError('conflicting retained decision review revision')
            return
        self._execute_native('INSERT INTO decision_reviews VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (task_id, wait_id, revision, json.dumps(payload), time.time()), write=True)
        retained = self._execute_native('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s', (task_id, wait_id, revision)).fetchone()
        if retained is None or json.loads(retained['payload']) != json.loads(json.dumps(payload)):
            raise ValueError('conflicting retained decision review revision')

    def decision_review(self, task_id: str, wait_id: str, revision: str | None=None) -> dict | None:
        if revision:
            row = self._execute_native('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s', (task_id, wait_id, revision)).fetchone()
        else:
            row = self._execute_native('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s ORDER BY created_at DESC LIMIT 1', (task_id, wait_id)).fetchone()
        return json.loads(row['payload']) if row else None

    def save_decision_draft(self, task_id: str, wait_id: str, revision: str, payload: dict) -> str:
        draft_id = secrets.token_urlsafe(18)
        self._execute_native('INSERT INTO decision_drafts VALUES (%s,%s,%s,%s,%s,%s)', (task_id, wait_id, revision, draft_id, json.dumps(payload), time.time()), write=True)
        return draft_id

    def decision_draft(self, task_id: str, wait_id: str, revision: str, draft_id: str) -> dict | None:
        row = self._execute_native('SELECT payload FROM decision_drafts WHERE task_id=%s AND wait_id=%s AND revision=%s AND draft_id=%s', (task_id, wait_id, revision, draft_id)).fetchone()
        return json.loads(row['payload']) if row else None

    def list_feedback(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self._execute_native('SELECT * FROM answer_feedback WHERE task_id=%s ORDER BY id', (task_id,))]

    def upsert_feedback(self, task_id: str, input_ref: str, target: str, content: str, thumb: int | None, comment: str | None, actor: str) -> dict:
        if target not in ('question', 'answer'):
            raise ValueError('target must be question or answer')
        if thumb not in (None, 1, -1):
            raise ValueError('thumb must be 1, -1, or null')
        session_row = self._execute_native('SELECT engine_session_id FROM tasks WHERE task_id=%s',(task_id,)).fetchone()
        session = session_row['engine_session_id'] if session_row else None
        now = time.time()
        self._execute_native('INSERT INTO answer_feedback(task_id,session_id,input_ref,target,content,thumb,comment,actor,created_at,updated_at)\n                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)\n                   ON CONFLICT(task_id,input_ref,target,actor)\n                   DO UPDATE SET session_id=excluded.session_id, content=excluded.content,\n                                 thumb=excluded.thumb, comment=excluded.comment, updated_at=excluded.updated_at', (task_id, session, input_ref, target, content, thumb, comment, actor, now, now), write=True)
        row = self._execute_native('SELECT * FROM answer_feedback WHERE task_id=%s AND input_ref=%s AND target=%s AND actor=%s', (task_id, input_ref, target, actor)).fetchone()
        self.audit(actor, 'feedback.upsert', task_id, {'feedback_id': row['id'], 'target': target, 'thumb': thumb, 'has_comment': bool(comment)})
        return dict(row)

    def _feedback_row(self, task_id: str, feedback_id: int, actor: str, admin: bool) -> dict:
        row = self._execute_native('SELECT * FROM answer_feedback WHERE id=%s AND task_id=%s', (feedback_id, task_id)).fetchone()
        if row is None:
            raise KeyError('feedback not found')
        if row['actor'] != actor and (not admin):
            raise PermissionError('feedback belongs to another actor')
        return row

    def update_feedback(self, task_id: str, feedback_id: int, actor: str, *, thumb: int | None=None, comment: str | None=None, admin: bool=False) -> dict:
        if thumb not in (None, 1, -1):
            raise ValueError('thumb must be 1, -1, or null')
        self._feedback_row(task_id, feedback_id, actor, admin)
        self._execute_native('UPDATE answer_feedback SET thumb=%s, comment=%s, updated_at=%s WHERE id=%s', (thumb, comment, time.time(), feedback_id), write=True)
        row = self._execute_native('SELECT * FROM answer_feedback WHERE id=%s', (feedback_id,)).fetchone()
        self.audit(actor, 'feedback.update', task_id, {'feedback_id': feedback_id, 'target': row['target'], 'thumb': thumb, 'has_comment': bool(comment)})
        return dict(row)

    def delete_feedback(self, task_id: str, feedback_id: int, actor: str, *, admin: bool=False) -> None:
        row = self._feedback_row(task_id, feedback_id, actor, admin)
        self._execute_native('DELETE FROM answer_feedback WHERE id=%s', (feedback_id,), write=True)
        self.audit(actor, 'feedback.delete', task_id, {'feedback_id': feedback_id, 'target': row['target']})

    def configure_simulation(self, task_id: str, source_dir: str, model_route: str) -> None:
        self._execute_native('UPDATE tasks SET simulated=1,source_dir=%s,model_route=%s,updated_at=%s WHERE task_id=%s', (source_dir, model_route, time.time(), task_id), write=True)

    def set_gate_open(self, task_id: str, gate_key: str, is_open: bool, actor: str='local user') -> dict:
        self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
        self._execute_native('UPDATE gates SET is_open=%s,revision=revision+1 WHERE task_id=%s AND gate_key=%s', (int(is_open), task_id, gate_key), write=True)
        self.audit(actor, 'gate.toggled', task_id, {'gate': gate_key, 'open': is_open, 'provenance': 'interactive V gate'})
        row = self._execute_native('SELECT * FROM gates WHERE task_id=%s AND gate_key=%s', (task_id, gate_key)).fetchone()
        if not row:
            raise KeyError(gate_key)
        return self._gate_record(row)

    def wait_at_gate(self, task_id: str, after_phase: str) -> dict | None:
        self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
        row = self._execute_native('SELECT * FROM gates WHERE task_id=%s AND after_phase=%s', (task_id, after_phase)).fetchone()
        if not row:
            return None
        now = time.time()
        if row['is_open']:
            self._execute_native("UPDATE gates SET state='bypassed',revision=revision+1 WHERE task_id=%s AND gate_key=%s", (task_id, row['gate_key']), write=True)
            self._execute_native("UPDATE phases SET state='approved',detail='Approved by explicit open-gate policy',completed_at=%s WHERE task_id=%s AND phase_key=%s", (now, task_id, after_phase), write=True)
            self.audit('policy', 'gate.bypassed', task_id, {'gate': row['gate_key'], 'after_phase': after_phase, 'approved_at': now, 'provenance': 'explicit open-gate policy'})
            return None
        self._execute_native("UPDATE phases SET state='awaiting_approval',detail='Generated; waiting for founder approval' WHERE task_id=%s AND phase_key=%s", (task_id, after_phase), write=True)
        self._execute_native("UPDATE gates SET state='waiting',revision=revision+1 WHERE task_id=%s AND gate_key=%s", (task_id, row['gate_key']), write=True)
        self._execute_native("UPDATE tasks SET state='waiting_gate',active_phase=%s,updated_at=%s WHERE task_id=%s", (after_phase, now, task_id), write=True)
        return self._gate_record(self._execute_native('SELECT * FROM gates WHERE task_id=%s AND gate_key=%s', (task_id, row['gate_key'])).fetchone())

    def decide_gate(self, task_id: str, gate_key: str, action: str, *, selected_option: str | None=None, edited_text: str | None=None, steer: str | None=None, actor: str='local user', expected_revision: int | None=None) -> dict:
        self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
        if action not in {'approve', 'edit', 'redo'}:
            raise ValueError('action must be approve, edit, or redo')
        row = self._execute_native('SELECT * FROM gates WHERE task_id=%s AND gate_key=%s', (task_id, gate_key)).fetchone()
        if not row:
            raise KeyError(gate_key)
        if expected_revision is not None and row['revision'] != expected_revision:
            raise ValueError('gate changed; refresh before deciding')
        if row['state'] != 'waiting':
            raise ValueError('gate is not waiting for a decision')
        if action == 'approve' and (not selected_option):
            raise ValueError('choose an option to approve')
        if action == 'edit' and (not edited_text):
            raise ValueError('edited option text is required')
        if action == 'redo' and (not steer):
            raise ValueError('a redo needs a reason so it is a steer, not a reroll')
        state = 'redo' if action == 'redo' else 'decided'
        now = time.time()
        phase = row['after_phase']
        self._execute_native('UPDATE gates SET state=%s,selected_option=%s,edited_text=%s,steer=%s,revision=revision+1 WHERE task_id=%s AND gate_key=%s', (state, selected_option, edited_text, steer, task_id, gate_key), write=True)
        if action == 'redo':
            self._execute_native("UPDATE phases SET state='running',detail=%s,completed=0,total=NULL,started_at=%s,completed_at=NULL WHERE task_id=%s AND phase_key=%s", (f'Redo requested: {steer}', now, task_id, phase), write=True)
            downstream = [definition.key for definition in followers(phase)]
            if downstream:
                marks = ','.join(('%s' for _ in downstream))
                self._execute_native(f"UPDATE phases SET state='pending',detail='',completed=0,total=NULL,started_at=NULL,completed_at=NULL WHERE task_id=%s AND phase_key IN ({marks})", (task_id, *downstream), write=True)
        else:
            detail = 'Founder approved edited artifact' if action == 'edit' else 'Founder approved'
            self._execute_native("UPDATE phases SET state='approved',detail=%s,completed_at=%s WHERE task_id=%s AND phase_key=%s", (detail, now, task_id, phase), write=True)
        self.audit(actor, f'gate.{action}', task_id, {'gate': gate_key, 'phase': phase, 'selected_option': selected_option, 'edited_text': edited_text, 'steer': steer, 'approved_at': now if action != 'redo' else None, 'provenance': 'interactive V gate'})
        task_state = 'done' if action != 'redo' and phase == PHASES[-1][0] else 'running'
        self._execute_native('UPDATE tasks SET state=%s,active_phase=%s,updated_at=%s WHERE task_id=%s', (task_state, phase, now, task_id), write=True)
        return self._gate_record(self._execute_native('SELECT * FROM gates WHERE task_id=%s AND gate_key=%s', (task_id, gate_key)).fetchone())

    def update_phase(self, task_id: str, phase_key: str, state: str, *, detail: str='', completed: int=0, total: int | None=None) -> None:
        self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
        now = time.time()
        current = self._execute_native('SELECT state,started_at FROM phases WHERE task_id=%s AND phase_key=%s', (task_id, phase_key)).fetchone()
        if not current:
            raise KeyError(phase_key)
        started = current['started_at'] or (now if state == 'running' else None)
        ended = now if state in ('done', 'approved', 'awaiting_approval') else None
        if state == 'running':
            earlier = [phase.key for phase in predecessors(phase_key)]
            later = [phase.key for phase in followers(phase_key)]
            if earlier:
                marks = ','.join(('%s' for _ in earlier))
                self._execute_native(f"UPDATE phases SET state='awaiting_approval',detail=CASE WHEN detail='' THEN 'Generated; waiting for founder approval' ELSE detail END,completed_at=%s WHERE task_id=%s AND phase_key IN ({marks}) AND state='running'", (now, task_id, *earlier), write=True)
            if later:
                marks = ','.join(('%s' for _ in later))
                self._execute_native(f"UPDATE phases SET state='pending',detail='Blocked on upstream approval',completed=0,total=NULL,started_at=NULL,completed_at=NULL WHERE task_id=%s AND phase_key IN ({marks}) AND state IN ('running','awaiting_approval')", (task_id, *later), write=True)
        self._execute_native('UPDATE phases SET state=%s,detail=%s,completed=%s,total=%s,started_at=%s,completed_at=%s WHERE task_id=%s AND phase_key=%s', (state, detail, completed, total, started, ended, task_id, phase_key), write=True)
        task_state = 'running' if state == 'running' else 'failed' if state == 'failed' else None
        if state in ('done', 'approved') and phase_key == PHASES[-1][0]:
            task_state = 'done'
        if task_state:
            # Late background completion may update its phase diagnostics, but
            # cannot undo the owner's already committed terminal Stop marker.
            self._execute_native('UPDATE tasks SET state=%s,active_phase=%s,updated_at=%s WHERE task_id=%s AND stop_requested_at IS NULL', (task_state, phase_key, now, task_id), write=True)
        else:
            self._execute_native('UPDATE tasks SET updated_at=%s WHERE task_id=%s', (now, task_id), write=True)

    def reconcile_completed_interview(self, task_id: str, session_id: str, sequence: int) -> bool:
        """Commit the missing HTTP interview boundary once; preserve downstream work."""
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone():
            return False
        row = self._execute_native('SELECT engine_session_id,state,active_phase FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row or row['engine_session_id'] != session_id:
            return False
        phases = self._execute_native("SELECT phase_key,state FROM phases WHERE task_id=%s AND phase_key IN ('intent','conops')", (task_id,)).fetchall()
        states = {phase['phase_key']: phase['state'] for phase in phases}
        if states.keys() != {'intent', 'conops'}:
            return False
        if row['active_phase'] not in ('intent', 'conops') or row['state'] not in ('running', 'failed'):
            return False
        if states['conops'] in ('approved', 'done'):
            return False
        if any((value not in ('pending', 'running', 'failed', 'awaiting_approval') for value in states.values())):
            return False
        now = time.time()
        for phase in ('intent', 'conops'):
            self._execute_native("UPDATE phases SET state='approved',detail='Founder approved in the completed interview',started_at=COALESCE(started_at,%s),completed_at=%s WHERE task_id=%s AND phase_key=%s", (now, now, task_id, phase), write=True)
        self._execute_native("UPDATE tasks SET state='running',active_phase='conops',updated_at=%s WHERE task_id=%s", (now, task_id), write=True)
        self._execute_native('INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s)', (task_id, 'conops', 'progress', 'Completed HTTP interview reconciled with V-process', json.dumps({'session_id': session_id, 'sequence': sequence, 'source': 'terminal_engine_snapshot', 'handoff': 'needs'}), now), write=True)
        self.audit('engine', 'interview.reconciled', task_id, {'session_id': session_id, 'sequence': sequence, 'next_phase': 'needs'})
        self._connection.get().notify_after_commit(task_id)
        return True

    def claim_failed_needs_retry(self, task_id: str, actor: str) -> bool:
        """Atomically claim only a failed Needs run with approved interview inputs."""
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone():
            return False
        row = self._execute_native('SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row or row['state'] != 'failed' or row['active_phase'] != 'needs' or (not row['engine_session_id']):
            return False
        states = {p['phase_key']: p['state'] for p in self._execute_native('SELECT phase_key,state FROM phases WHERE task_id=%s', (task_id,))}
        if any((states.get(phase) not in ('approved', 'done') for phase in ('intent', 'conops'))) or states.get('needs') != 'failed':
            return False
        if any((states.get(phase) not in ('pending', 'failed') for phase in ('srs', 'architecture', 'specs', 'tests', 'validation'))):
            return False
        now = time.time()
        self._execute_native("UPDATE phases SET state='running',detail='Retrying failed Stakeholder needs',completed=0,total=NULL,started_at=%s,completed_at=NULL WHERE task_id=%s AND phase_key='needs'", (now, task_id), write=True)
        self._execute_native("UPDATE tasks SET state='running',active_phase='needs',updated_at=%s WHERE task_id=%s", (now, task_id), write=True)
        self._execute_native('INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s)', (task_id, 'needs', 'retry', 'Founder confirmed retry of failed Stakeholder needs', json.dumps({'actor': actor, 'session_id': row['engine_session_id']}), now), write=True)
        self.audit(actor, 'phase.retry', task_id, {'phase': 'needs', 'session_id': row['engine_session_id']})
        self._connection.get().notify_after_commit(task_id)
        return True

    def claim_next_phase(self, task_id: str, phase: str, actor: str) -> bool:
        """Atomically move a task whose interview is approved into its first generator phase.

            A second click finds the phase already running and gets False, so one click starts one job."""
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone():
            return False
        from .phases import PHASE_KEYS, PHASE_BY_KEY
        if phase != 'needs' or phase not in PHASE_BY_KEY:
            return False
        row = self._execute_native('SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row or row['state'] != 'running' or row['active_phase'] != 'conops' or (not row['engine_session_id']):
            return False
        states = {p['phase_key']: p['state'] for p in self._execute_native('SELECT phase_key,state FROM phases WHERE task_id=%s', (task_id,))}
        if any((states.get(key) not in ('approved', 'done') for key in ('intent', 'conops'))) or states.get(phase) != 'pending':
            return False
        if any((states.get(key) != 'pending' for key in PHASE_KEYS[PHASE_KEYS.index(phase) + 1:])):
            return False
        now = time.time()
        self._execute_native("UPDATE phases SET state='running',detail=%s,completed=0,total=NULL,started_at=%s,completed_at=NULL WHERE task_id=%s AND phase_key=%s", (f'Starting {PHASE_BY_KEY[phase].label}', now, task_id, phase), write=True)
        self._execute_native("UPDATE tasks SET state='running',active_phase=%s,updated_at=%s WHERE task_id=%s", (phase, now, task_id), write=True)
        self._execute_native('INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s)', (task_id, phase, 'progress', f'You started {PHASE_BY_KEY[phase].label}', json.dumps({'actor': actor, 'session_id': row['engine_session_id'], 'started': True}), now), write=True)
        self.audit(actor, 'phase.start', task_id, {'phase': phase, 'session_id': row['engine_session_id']})
        self._connection.get().notify_after_commit(task_id)
        return True

    def claim_failed_phase_retry(self, task_id: str, phase: str, actor: str) -> bool:
        """Atomically claim a failed generator phase whose upstream phases are approved.

            Later phases may only be pending or failed, so a retry never overwrites finished work."""
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone():
            return False
        from .phases import PHASE_KEYS, PHASE_BY_KEY
        if phase not in PHASE_BY_KEY or PHASE_BY_KEY[phase].executor != 'generator':
            return False
        row = self._execute_native('SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row or row['state'] != 'failed' or row['active_phase'] != phase or (not row['engine_session_id']):
            return False
        states = {p['phase_key']: p['state'] for p in self._execute_native('SELECT phase_key,state FROM phases WHERE task_id=%s', (task_id,))}
        index = PHASE_KEYS.index(phase)
        if states.get(phase) != 'failed':
            return False
        if any((states.get(key) not in ('approved', 'done') for key in PHASE_KEYS[:index])):
            return False
        if any((states.get(key) not in ('pending', 'failed') for key in PHASE_KEYS[index + 1:])):
            return False
        now = time.time()
        self._execute_native("UPDATE phases SET state='running',detail=%s,completed=0,total=NULL,started_at=%s,completed_at=NULL WHERE task_id=%s AND phase_key=%s", (f'Retrying failed {PHASE_BY_KEY[phase].label}', now, task_id, phase), write=True)
        self._execute_native("UPDATE tasks SET state='running',active_phase=%s,updated_at=%s WHERE task_id=%s", (phase, now, task_id), write=True)
        self._execute_native('INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (%s,%s,%s,%s,%s,%s)', (task_id, phase, 'retry', f'Founder confirmed retry of failed {PHASE_BY_KEY[phase].label}', json.dumps({'actor': actor, 'session_id': row['engine_session_id']}), now), write=True)
        self.audit(actor, 'phase.retry', task_id, {'phase': phase, 'session_id': row['engine_session_id']})
        self._connection.get().notify_after_commit(task_id)
        return True

    def set_model_route(self, task_id: str, model_route: str) -> None:
        self._execute_native('UPDATE tasks SET model_route=%s,updated_at=%s WHERE task_id=%s', (model_route, time.time(), task_id), write=True)

    def set_model_default(self, task_id: str, model: str) -> None:
        """Persist the exact model selected by the founder for subsequent phases."""
        self._execute_native('UPDATE tasks SET model_default=%s,updated_at=%s WHERE task_id=%s', (model, time.time(), task_id), write=True)

    def task_for_engine_session(self, session_id: str) -> str | None:
        from . import session_task_cache
        # Same-transaction writer or an uncommitted mapping change: read the database, store nothing.
        cacheable = not self._map_dirty.get()
        read_version = session_task_cache.version(self)
        row = self._execute_native('SELECT task_id FROM tasks WHERE engine_session_id=%s AND deleted_at IS NULL',(session_id,)).fetchone()
        task_id = str(row['task_id']) if row else None
        if cacheable:session_task_cache.put(self, session_id, task_id, read_version=read_version)
        return task_id

    def engine_session(self, task_id: str) -> str | None:
        row = self._execute_native('SELECT engine_session_id FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        return row['engine_session_id'] if row else None

    def bind_interview_schema(self, task_id: str, schema_id: str, schema_version: str) -> dict:
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone():
            raise KeyError(task_id)
        row = self._execute_native('SELECT interview_schema_id,interview_schema_version FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        existing = (row['interview_schema_id'], row['interview_schema_version'])
        requested = (schema_id, schema_version)
        if existing != (None, None) and existing != requested:
            raise ValueError('interview schema binding is immutable for a task generation')
        self._execute_native('UPDATE tasks SET interview_schema_id=%s,interview_schema_version=%s,updated_at=%s WHERE task_id=%s', (schema_id, schema_version, time.time(), task_id), write=True)
        return {'task_id': task_id, 'schema_id': schema_id, 'schema_version': schema_version}

    def interview_schema_binding(self, task_id: str) -> dict | None:
        row = self._execute_native('SELECT interview_schema_id,interview_schema_version FROM tasks WHERE task_id=%s', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        if row['interview_schema_id'] is None:
            return None
        return {'task_id': task_id, 'schema_id': row['interview_schema_id'], 'schema_version': row['interview_schema_version']}

    def rerun_prerequisite(self, task_id: str, from_phase: str) -> dict | None:
        """Return the first unfulfilled upstream phase, or None when rerun is valid."""
        positions = PHASE_POSITIONS
        if from_phase not in positions:
            raise KeyError(from_phase)
        phases = self._execute_native('SELECT phase_key,label,state FROM phases WHERE task_id=%s ORDER BY position', (task_id,)).fetchall()
        if not phases:
            raise KeyError(task_id)
        for phase in phases[:positions[from_phase]]:
            if phase['state'] not in ('approved', 'done'):
                return dict(phase)
        return None

    def link(self, token: str) -> dict | None:
        row = self._execute_native('SELECT * FROM links WHERE token=%s AND active=1', (token,)).fetchone()
        return dict(row) if row else None

    def deactivate_link(self, token: str) -> None:
        self._execute_native('UPDATE links SET active=0 WHERE token=%s', (token,), write=True)

    def set_state(self, run_id: str, state: RunState) -> None:
        self._execute_native('UPDATE runs SET state=%s, updated_at=%s WHERE run_id=%s', (state.value, time.time(), run_id), write=True)

    def run(self, run_id: str) -> dict | None:
        row = self._execute_native('SELECT * FROM runs WHERE run_id=%s', (run_id,)).fetchone()
        return dict(row) if row else None

    def set_submission(self, run_id: str, submission: str) -> None:
        self._execute_native('UPDATE runs SET submission=%s,updated_at=%s WHERE run_id=%s', (submission, time.time(), run_id), write=True)

    def total_spend(self) -> float:
        return self._execute_native('SELECT COALESCE(SUM(cost_usd),0) t FROM runs').fetchone()['t']

    def add_artifact(self, run_id: str, kind: str, ref: str) -> None:
        self._execute_native('INSERT INTO artifacts VALUES (%s,%s,%s,%s)', (run_id, kind, ref, time.time()), write=True)

    def artifacts(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._execute_native('SELECT * FROM artifacts WHERE run_id=%s', (run_id,))]

    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str, mime_type: str='application/json') -> None:
        task = self.task(task_id)
        self._execute_native('INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)', (task_id, task['generation'], phase_key, name, ref, mime_type, time.time()), write=True)

    def quota_allows(self, user_id: str) -> tuple[bool, dict]:
        q = self.user_quota(user_id)
        if not q:
            return (False, {'user_id': user_id, 'token_quota': 0, 'tokens_used': 0})
        return (q['tokens_used'] < q['token_quota'], q)

    def add_user_usage(self, run_id: str, user_id: str, record_json: str, cost_usd: float, input_tokens: int, output_tokens: int) -> float:
        if user_id != current_scope().owner_id:
            raise TenantAccessDenied('cannot attribute usage to another owner')
        self._execute_native('INSERT INTO usage(run_id,record,ts,user_id,input_tokens,output_tokens) VALUES(%s,%s,%s,%s,%s,%s)', (run_id, record_json, time.time(), user_id, input_tokens, output_tokens), write=True)
        self._execute_native('UPDATE runs SET cost_usd=cost_usd+%s,updated_at=%s WHERE run_id=%s', (cost_usd, time.time(), run_id), write=True)
        return self.run(run_id)['cost_usd']

    def create_task(self,title,brief='',*,task_id=None,is_test=False):
        if type(is_test) is not bool:raise ValueError('boolean test marker required')
        import psycopg
        title=title.strip()
        if not title: raise ValueError('task name cannot be empty')
        task_id,now=task_id or secrets.token_hex(6),time.time()
        owner=current_scope().owner_id
        try:
            self._execute_native('INSERT INTO tasks(task_id,title,brief,state,model_route,generation,created_at,updated_at,owner_id,is_test) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                             (task_id,title,brief,'not_started','deepseek',1,now,now,owner,is_test), write=True)
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f'a task named {title!r} already exists') from exc
        for i,(key,label) in enumerate(PHASES):
            self._execute_native("INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (%s,%s,%s,%s,'pending')",
                                 (task_id,key,label,i), write=True)
            self._execute_native("INSERT INTO gates(task_id,gate_key,after_phase,label,position,is_open,state,options) VALUES (%s,%s,%s,%s,%s,0,'idle',%s)",
                                 (task_id,f'gate-{key}',key,f'{label} gate',i,json.dumps([f'Approve {label}','Choose another option'])), write=True)
        self.audit(owner,'task.created',task_id,{'title':title})
        return self.task(task_id)

    def create_link(self,founder_label):
        token,run_id,now=secrets.token_urlsafe(24),secrets.token_hex(8),time.time()
        self._execute_native('INSERT INTO runs(run_id,state,budget_usd,created_at,updated_at,owner_id) VALUES (%s,%s,%s,%s,%s,%s)',
                         (run_id,RunState.CREATED.value,6.,now,now,current_scope().owner_id), write=True)
        self._execute_native('INSERT INTO links VALUES (%s,%s,%s,1,%s)',(token,run_id,founder_label,now), write=True)
        return {'token':token,'run_id':run_id}

    def add_usage(self,run_id,record_json,cost_usd):
        return self.add_user_usage(run_id,current_scope().owner_id,record_json,cost_usd,0,0)

    def attribute_run(self,run_id,user_id):
        if user_id != current_scope().owner_id: raise TenantAccessDenied('cannot change run owner')
        if not self.run(run_id): raise KeyError(run_id)

    def user_quota(self,user_id):
        if user_id != current_scope().owner_id: return None
        row=self._execute_native('SELECT user_id,label,token_quota FROM users WHERE user_id=%s',(user_id,)).fetchone()
        if row is None: return None
        out=dict(row)
        out['user_id']=str(out['user_id'])
        out['tokens_used']=self._execute_native('SELECT COALESCE(SUM(input_tokens+output_tokens),0) n FROM usage WHERE user_id=%s',(user_id,)).fetchone()['n']
        out['email']=None;out['is_admin']=0
        return out

    # One-statement PostgreSQL projection:
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

    # PG-native ordered bounded event projection.
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
        return self._ARTIFACTS_SUBQUERY + ",\n" + self._MANIFEST_SUBQUERY

    def list_tasks(self):
        rows=self._execute_native(self._TASK_PROJECTION+' WHERE t.deleted_at IS NULL AND NOT t.is_test ORDER BY t.updated_at DESC').fetchall()
        return [self._task_record(row) for row in rows]

    def list_test_tasks(self):
        if not self._execute_native('SELECT public.requi_test_task_owner() AS allowed').fetchone()['allowed']:
            raise TenantAccessDenied('reviewed test owner required')
        return [dict(row) for row in self._execute_native('SELECT task_id,state,is_test,test_purged_at,created_at FROM tasks WHERE is_test ORDER BY created_at DESC LIMIT 100').fetchall()]

    def test_task_expense(self,task_id):
        if not self._execute_native('SELECT public.requi_test_task_owner() AS allowed').fetchone()['allowed']:
            raise TenantAccessDenied('reviewed test owner required')
        task=self.task(task_id)
        if not task or not task['is_test']:raise KeyError('owned test task required')
        rows=self._execute_native("""SELECT p.state,p.expense_actual,p.quote->>'expense_units_per_usd' AS scale
            FROM provider_attempts p JOIN worker_jobs j USING(job_id) WHERE j.task_id=%s""",(task_id,)).fetchall()
        from decimal import Decimal
        total=Decimal(0);complete=bool(rows)
        for row in rows:
            if row['state']=='cancelled':continue
            if row['state']!='settled' or row['expense_actual'] is None or not row['scale'] or not row['scale'].isdigit() or int(row['scale'])<=0:
                complete=False
            else:total+=Decimal(row['expense_actual'])/Decimal(row['scale'])
        return {'provider_usd':str(total) if complete else None,'complete':complete,'attempts':len(rows),'test':True}

    def test_task_breakdown(self,task_id):
        from .acceptance_evidence import breakdown
        return breakdown(self,task_id)

    def purge_test_task(self,task_id,*,confirm=False):
        row=self._execute_native('SELECT public.requi_purge_test_task(%s,%s) AS result',(task_id,confirm),write=True).fetchone()
        return row['result']

    def task(self,task_id):
        row=self._execute_native(self._TASK_PROJECTION+' WHERE t.task_id=%s AND t.deleted_at IS NULL',(task_id,)).fetchone()
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
            from . import session_task_cache
            if session_task_cache.affects_mapping(sql):
                session_task_cache.invalidate(self);self._map_dirty.set(True)
                if shared is not None:shared.map_invalidations.add(self)
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
        task=self._execute_native('SELECT * FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        if not task or task['deleted_at'] is not None:raise KeyError(task_id)
        if task['stop_requested_at'] is None:
            self._execute_native("UPDATE tasks SET state='stop_requested',generation=generation+1,stop_requested_at=%s,updated_at=%s WHERE task_id=%s",(time.time(),time.time(),task_id), write=True)
            # A running answer's stop watcher waits on this key instead of polling; woken after commit.
            from .stop_watch import stop_key
            self._connection.get().notify_after_commit(stop_key(task_id))
        return self.task(task_id)

    def confirm_child_session_quiescent(self,session_id):
        """Reaped child evidence, never lease expiry or an empty local registry.

        Stop fenced the generation first. All jobs in this session must have
        finished and have durable parent evidence after descriptor quiescence.
        A crash/restart without this evidence stays pending.
        """
        binding=self.session_binding(session_id)
        if not binding:return False
        tid=binding['task_id'];task=self.task(tid)
        if not task or task.get('stop_requested_at') is None:return False
        row=self._execute_native("SELECT control FROM engine_sessions WHERE session_id=%s FOR UPDATE",(session_id,)).fetchone()
        if not row or not (row['control'] or {}).get('stopped'):return False
        jobs=self._execute_native('SELECT job_id,state FROM worker_jobs WHERE task_id=%s AND session_id=%s',(tid,session_id)).fetchall()
        if not jobs:return False
        for job in jobs:
            proof=self.decision_review(tid,'child-job:'+job['job_id'],'child-completion-v1')
            if job['state']!='done' or not proof or proof.get('reaped') is not True:return False
        if self._execute_native("SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s AND p.state!='settled' LIMIT 1",(tid,)).fetchone():return False
        if self._execute_native("SELECT 1 FROM retail_compute_segments r JOIN worker_jobs j ON j.job_id=r.job_id WHERE j.task_id=%s AND r.state!='settled' LIMIT 1",(tid,)).fetchone():return False
        self._execute_native("UPDATE engine_sessions SET control=control || '{\"stop_quiescent\":true}'::jsonb WHERE session_id=%s",(session_id,),write=True)
        return True

    def confirm_task_stopped(self,task_id,*,engine_quiescent):
        from .attempt_ledger import AdmissionDenied
        if engine_quiescent is not True:raise AdmissionDenied('engine quiescence required')
        task=self._execute_native('SELECT * FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        if not task or task['stop_requested_at'] is None:raise AdmissionDenied('durable stop request required')
        if self._execute_native("SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s AND p.state IN ('dispatched','uncertain','reserved') LIMIT 1",(task_id,)).fetchone():
            raise AdmissionDenied('provider attempt is not reconciled')
        # Closed work stays for accounting. Pending dispatch is fenced.
        self._execute_native("UPDATE worker_jobs SET state='done',updated_at=%s WHERE task_id=%s AND state IN ('pending','paused')",(time.time(),task_id), write=True)
        if self._execute_native("SELECT 1 FROM worker_jobs WHERE task_id=%s AND state='running' LIMIT 1",(task_id,)).fetchone():
            raise AdmissionDenied('worker remains active')
        if task['stopped_at'] is None:
            self._execute_native("UPDATE tasks SET state='stopped',stopped_at=%s,updated_at=%s WHERE task_id=%s",(time.time(),time.time(),task_id), write=True)
        return self.task(task_id)

    def tombstone_stopped_task(self,task_id):
        from .attempt_ledger import AdmissionDenied
        task=self._execute_native('SELECT * FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        if not task or task['state']!='stopped' or task['stopped_at'] is None:
            raise AdmissionDenied('confirmed stopped task required')
        # Preserve FK chain, raw usage, charges and existing source artifacts.
        if task['deleted_at'] is None:
            self._execute_native('UPDATE tasks SET deleted_at=%s,updated_at=%s WHERE task_id=%s',(time.time(),time.time(),task_id), write=True)
        return self._task_record(self._execute_native(self._TASK_PROJECTION+' WHERE t.task_id=%s',(task_id,)).fetchone())

    def delete_task(self,task_id,actor='local user',*,confirm=True):
        return self.stop_hide_task(task_id,confirm=confirm,actor=actor)

    def set_engine_session(self,task_id,session_id):
        task=self.task(task_id)
        if not task: raise KeyError(task_id)
        existing=self._execute_native('SELECT * FROM engine_sessions WHERE session_id=%s',(session_id,)).fetchone()
        if existing and (existing['task_id'] != task_id or existing['generation'] != task['generation']):
            raise TenantAccessDenied('engine session binding is immutable')
        if not existing:
            self._execute_native('INSERT INTO engine_sessions VALUES (%s,%s,%s,%s,%s)',
                (session_id,current_scope().owner_id,task_id,task['generation'],time.time()), write=True)
        self._execute_native('UPDATE tasks SET engine_session_id=%s,updated_at=%s WHERE task_id=%s',(session_id,time.time(),task_id), write=True)

    def begin_interview_start(self,task_id,session_id,route):
        now = time.time()
        result = self._execute_native(
            "UPDATE tasks SET state='running',active_phase='intent',engine_session_id=%s,model_route=%s,updated_at=%s "
            "WHERE task_id=%s AND state='not_started' AND engine_session_id IS NULL",
            (session_id,route,now,task_id), write=True)
        if result.rowcount != 1:
            self._connection.get().execute('ROLLBACK TO SAVEPOINT operation_start')
            return False
        self._execute_native("UPDATE phases SET state='running',detail='Starting interview',started_at=%s WHERE task_id=%s AND phase_key='intent'",
                             (now,task_id), write=True)
        self.add_event(task_id,'command','Interview starting',phase_key='intent',payload={'session_id':session_id,'route':route})
        self.set_engine_session(task_id,session_id)
        return True

    def session_binding(self,session_id):
        row=self._execute_native('SELECT * FROM engine_sessions WHERE session_id=%s',(session_id,)).fetchone()
        if not row:return None
        result=dict(row);result['owner_id']=str(result['owner_id']);return result

    def installed_execution_anchor(self,session_id):
        """Read exact immutable installed binding, no file-derived authority."""
        from vmodel_contracts.execution_anchor import ExecutionAnchor
        import hashlib
        row=self._execute_native('SELECT * FROM installed_execution_bindings WHERE session_id=%s',(session_id,)).fetchone()
        if not row:return None
        intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s',(row['installation_id'],)).fetchone()
        latest=self._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(row['installation_id'],)).fetchone()
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
        self._execute_native("INSERT INTO worker_jobs (job_id,owner_id,auth_session_id,task_id,session_id,generation,kind,state,attempt,created_at,updated_at,execution_options) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',0,%s,%s,%s::jsonb)",
           (job_id,capability.scope.owner_id,capability.scope.session_id,task_id,
            capability.session_id,capability.generation,kind,now,now,json.dumps(execution_options or {})), write=True)
        return job_id

    def prepare_phase_retry(self,task_id,phase,actor,model,checkpoint):
        """One durable transaction for failed-phase claim, model and pending job.

        Lock the owned task before checking eligibility so concurrent confirms
        cannot each reserve a job. Public operation wrapper owns commit/rollback.
        """
        row=self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
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
        row=self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            task=self.task(task_id);scope=current_scope()
            if task['generation']!=proposal['generation'] or proposal['task_id']!=task_id:
                raise ValueError('reviewed restart generation/task changed')
            binding=self.session_binding(proposal['session_id'])
            if not binding or binding['task_id']!=task_id or binding['generation']!=task['generation']:
                raise TenantAccessDenied('reviewed restart session changed')
            prior=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE owner_id=%s AND request_key=%s',
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
            self._execute_native('INSERT INTO closed_revision_restart_intents(intent_id,request_key,owner_id,auth_session_id,task_id,source_generation,gate_key,fingerprint,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (intent_id,request_key,scope.owner_id,scope.session_id,task_id,task['generation'],options['gate_key'],fingerprint,json.dumps(body,sort_keys=True),time.time()), write=True)
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            intent=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s AND task_id=%s',
                (intent_id,task_id)).fetchone()
            if not intent:raise KeyError(intent_id)
            held=json.loads(intent['payload'])
            if held['state']!='held' or held['proposal']!=retained_proposal.get('proposal'):
                raise ValueError('lineage proposal differs from held reviewed intent')
            planned=plan_revision_lineage(capability=capability,semantic_session_id=semantic_session_id,
                new_execution_session_id=new_execution_session_id,options=held['options'],retained_proposal=retained_proposal)
            prior=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE intent_id=%s',(intent_id,)).fetchone()
            if prior:
                if prior['fingerprint']!=planned['lineage_sha256']:raise ValueError('conflicting lineage plan replay')
                return {'lineage_id':prior['lineage_id'],'state':'planned','replayed':True,'installed':False}
            if self.session_binding(new_execution_session_id):raise ValueError('destination execution session already bound')
            scope=current_scope();lineage_id=secrets.token_hex(16);body=planned['lineage']
            self._execute_native('INSERT INTO closed_revision_lineage_plans(lineage_id,owner_id,auth_session_id,intent_id,task_id,parent_execution_session_id,parent_generation,execution_session_id,generation,semantic_session_id,fingerprint,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (lineage_id,scope.owner_id,scope.session_id,intent_id,task_id,capability.session_id,capability.generation,
                 new_execution_session_id,body['generation'],semantic_session_id,planned['lineage_sha256'],json.dumps(body,sort_keys=True),time.time()), write=True)
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            lineage=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',
                (lineage_id,task_id)).fetchone()
            if not lineage:raise KeyError(lineage_id)
            task=self.task(task_id)
            if task['generation']!=lineage['parent_generation'] or task['engine_session_id']!=lineage['parent_execution_session_id']:
                raise ValueError('journal parent generation/session changed')
            latest=self._execute_native('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=%s ORDER BY sequence DESC LIMIT 1',
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
            self._execute_native('INSERT INTO closed_revision_promotion_events(lineage_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (lineage_id,scope.owner_id,scope.session_id,body['sequence'],state,event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),time.time()), write=True)
            self.audit(scope.owner_id,'gate.revision_preparation_journal',task_id,{'lineage_id':lineage_id,'state':state,'event_sha256':event['event_sha256']})
            return event

    def reconcile_private_revision_installation(self,task_id,*,installation_id,artifacts_base):
        """Concrete read-only current files/DB classification, never resumes."""
        from .revision_install_recovery import classify_installation_recovery
        from .revision_install_plan import _validate_plan
        from .revision_stage_evidence import read_validated_staged_receipt
        from .tenancy import session_root
        from .producer_inputs import _open_directory
        from vmodel_contracts.execution_anchor import read_execution_anchor
        from pathlib import Path
        from .historical_checkpoint import HistoricalCheckpointCapability
        import os
        with self._task_operation_guard('task.rerun'):
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
            event=self._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
            if not event:raise ValueError('installation journal missing')
            import hashlib
            journal=json.loads(event['payload'])
            if (hashlib.sha256(json.dumps(journal,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=event['event_sha256']
                or journal['plan_sha256']!=intent['plan_sha256']):raise ValueError('reconciliation journal digest changed')
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s',(intent['lineage_id'],)).fetchone()
            held=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s',(row['intent_id'],)).fetchone()
            lineage=json.loads(row['payload']);options=json.loads(held['payload'])['options'];scope=current_scope()
            errors=[];stage_present=False;files=False
            historical=HistoricalCheckpointCapability(scope,task_id,lineage['parent_execution_session_id'],lineage['parent_generation'])
            from .pg_revision_install import read_evidence
            from .pg_revision_stage import read_stage
            try:
                read_stage(self,historical,plan['staged_receipt'],lineage=lineage,lineage_sha256=row['fingerprint'])
                stage_present=True
                from .revision_source_review import read_source_review_snapshot
                retained=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(intent['lineage_id'],)).fetchone()
                snapshot=read_source_review_snapshot(self,historical,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=row['fingerprint'])
                if not retained or snapshot!={'snapshot':json.loads(retained['payload']),'snapshot_sha256':retained['fingerprint']}:
                    raise ValueError('reconciliation source differs from retained review')
                receipt=read_validated_staged_receipt(self,historical,artifacts_base=artifacts_base,lineage=lineage,
                    lineage_sha256=row['fingerprint'],manifest_sha256=plan['staged_receipt']['manifest_sha256'],options=options,promoted=True)
                if receipt!=plan['staged_receipt']:raise ValueError('reconciliation stage differs')
            except (OSError,ValueError,KeyError):errors.append('pg_stage_or_source_evidence_unverified')
            try:
                proof=read_evidence(self,task_id,installation_id)
                if proof!={k:v for k,v in journal['evidence'].items() if k!='execution_session_id'} or errors:
                    raise ValueError('promoted PG evidence differs from journal')
                files=True;stage_present=False
            except (OSError,ValueError,KeyError):
                if event['state'] not in ('authorized','promotion_pending'):errors.append('promoted_pg_evidence_unverified')
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
        from .revision_install_plan import _validate_plan
        from .tenancy import session_root
        from vmodel_contracts.execution_anchor import read_execution_anchor
        from pathlib import Path
        cap=WorkerCapability.capture(self,task_id);cap.validate(self)
        intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
        if not intent:raise KeyError(installation_id)
        plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
        row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s',(intent['lineage_id'],)).fetchone()
        source=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(intent['lineage_id'],)).fetchone()
        held=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s',(row['intent_id'],)).fetchone()
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
        from .pg_revision_install import read_evidence
        return read_evidence(self,task_id,installation_id)

    def activate_private_revision_installation(self,task_id,*,installation_id,expected_previous_sha256,validate_acceptance,artifacts_base):
        """Private concrete DB CAS after promoted files, never dispatch.

        No route invokes this method. Owner acceptance verifier must authenticate
        retained exact review and cost acknowledgement; test fixtures aren't it.
        """
        from .revision_install_plan import _validate_plan
        from .revision_install_journal import install_event
        from .phases import followers
        if not callable(validate_acceptance):raise ValueError('trusted acceptance verifier required')
        with self._task_operation_guard('task.rerun'):
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
            latest=self._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
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
            self._execute_native('INSERT INTO engine_sessions VALUES (%s,%s,%s,%s,%s)',(anchor.execution_session_id,scope.owner_id,task_id,anchor.generation,now), write=True)
            self._execute_native('INSERT INTO installed_execution_bindings(session_id,owner_id,auth_session_id,task_id,generation,semantic_session_id,installation_id,lineage_sha256,anchor_sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (anchor.execution_session_id,scope.owner_id,scope.session_id,task_id,anchor.generation,anchor.semantic_session_id,installation_id,anchor.lineage_sha256,evidence['anchor_sha256'],now), write=True)
            from .pg_revision_install import seed_accepted_checkpoint
            seed_accepted_checkpoint(self,task_id,installation_id,validate_acceptance)
            changed=self._execute_native("UPDATE tasks SET engine_session_id=%s,generation=%s,state='pending',active_phase=%s,updated_at=%s WHERE task_id=%s AND generation=%s AND engine_session_id=%s",
                (anchor.execution_session_id,anchor.generation,next(iter(followers(review['phase']))).key,now,task_id,parent['generation'],parent['execution_session_id']), write=True)
            if changed.rowcount!=1:raise ValueError('installation parent CAS failed')
            phase=review['phase'];later=[p.key for p in followers(phase)]
            self._execute_native("UPDATE phases SET state='approved',detail='Approved Exact Regenerated Result',completed_at=%s,gate_basis_changed=1 WHERE task_id=%s AND phase_key=%s",(now,task_id,phase), write=True)
            self._execute_native("UPDATE gates SET state='decided',is_open=0,selected_option='approve',edited_text=NULL,steer=NULL,revision=revision+1 WHERE task_id=%s AND after_phase=%s",(task_id,phase), write=True)
            for key in later:
                self._execute_native("UPDATE phases SET state='pending',detail='',completed=0,total=NULL,started_at=NULL,completed_at=NULL,gate_basis_changed=1 WHERE task_id=%s AND phase_key=%s",(task_id,key), write=True)
                self._execute_native("UPDATE gates SET state='idle',is_open=0,selected_option=NULL,edited_text=NULL,steer=NULL,revision=revision+1 WHERE task_id=%s AND after_phase=%s",(task_id,key), write=True)
            for key in [phase]+later:
                self._execute_native("UPDATE task_artifacts SET stale=1,stale_reason='reviewed lineage regeneration' WHERE task_id=%s AND phase_key=%s AND generation=%s",(task_id,key,parent['generation']), write=True)
            event=install_event(plan_sha256=intent['plan_sha256'],previous={'event':json.loads(latest['payload']),'event_sha256':latest['event_sha256']},
                state='installed',evidence=evidence|{'execution_session_id':anchor.execution_session_id})
            body=event['event']
            self._execute_native('INSERT INTO closed_revision_install_events(installation_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (installation_id,scope.owner_id,scope.session_id,body['sequence'],body['state'],event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),now), write=True)
            self.audit(scope.owner_id,'gate.private_revision_installed',task_id,{'installation_id':installation_id,'generation':anchor.generation,'approved_phase':phase,'review_sha256':intent['review_sha256'],
                'acceptance_reference':intent['acceptance_reference'],'next_phase':later[0] if later else None,'dispatch_started':False})
            return {'installation_id':installation_id,'event_sha256':event['event_sha256'],'installed':True,'dispatch_started':False,
                'approved_phase':phase,'next_phase':later[0] if later else None,'continuation_prepared':True}

    def private_approved_continuation_basis(self,task_id,*,installation_id,artifacts_base,expected_job_id=None):
        """Exact current DB approval and immutable installed files, no dispatch."""
        from .worker_capability import WorkerCapability
        from .revision_install_plan import _validate_plan
        with self._task_operation_guard('task.rerun'):
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
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
                cap.validate_job(job)
                if (job['kind']!='continuation:approved-revision'
                    or job['state'] not in ('pending','running') or job['execution_options']!={}):
                    raise ValueError('exact private continuation worker required')
            if (self._execute_native("SELECT 1 FROM worker_jobs WHERE task_id=%s AND state IN ('pending','running','paused','failed') AND job_id<>%s LIMIT 1",(task_id,expected_job_id or '')).fetchone()
                or self._execute_native("SELECT 1 FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s AND p.state IN ('reserved','dispatched','uncertain') AND j.job_id<>%s LIMIT 1",(task_id,expected_job_id or '')).fetchone()):
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
            if self._execute_native('SELECT 1 FROM decision_drafts WHERE task_id=%s AND wait_id=%s AND revision=%s LIMIT 1',(task_id,'revision-changes:'+lineage_id,review_sha256)).fetchone():
                raise ValueError('review superseded by further changes')
            scope=current_scope()
            if scope.session_id is None or not self.auth_session_active():raise TenantAccessDenied('active owner submission required')
            lineage=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',(lineage_id,task_id)).fetchone()
            intent=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s',(lineage['intent_id'],)).fetchone()
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
            receipts=self._execute_native('SELECT acceptance_id FROM closed_revision_acceptances WHERE lineage_id=%s AND review_sha256=%s',(lineage_id,review_sha256)).fetchall()
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
            if self._execute_native('SELECT 1 FROM decision_drafts WHERE task_id=%s AND wait_id=%s AND revision=%s LIMIT 1',(task_id,'revision-changes:'+lineage_id,review_sha256)).fetchone():
                raise ValueError('review superseded by further changes')
            scope=current_scope()
            if scope.session_id is None or not self.auth_session_active():raise TenantAccessDenied('active authenticated owner submission required')
            prior=self._execute_native('SELECT * FROM closed_revision_acceptances WHERE lineage_id=%s AND review_sha256=%s AND auth_session_id=%s',
                (lineage_id,review_sha256,scope.session_id)).fetchone()
            if prior:
                self.verify_private_revision_acceptance(current['review'],review_sha256,prior['acceptance_id'])
                return {'acceptance_reference':prior['acceptance_id'],'review_sha256':review_sha256,'replayed':True,'installed':False}
            import uuid
            acceptance_id='accept-'+uuid.uuid4().hex
            payload={'owner_id':scope.owner_id,'task_id':task_id,'lineage_id':lineage_id,'auth_session_id':scope.session_id,
                'review_sha256':review_sha256,'decision':'approve','extra_cost_acknowledged':True,
                'source':'private_authenticated_submission','installed':False,'dispatch_started':False}
            self._execute_native('INSERT INTO closed_revision_acceptances(acceptance_id,lineage_id,owner_id,auth_session_id,task_id,review_sha256,decision,extra_cost_acknowledged,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (acceptance_id,lineage_id,scope.owner_id,scope.session_id,task_id,review_sha256,'approve',True,json.dumps(payload,sort_keys=True),time.time()), write=True)
            return {'acceptance_reference':acceptance_id,'review_sha256':review_sha256,'replayed':False,'installed':False}

    def verify_private_revision_acceptance(self,review,review_sha256,acceptance_reference):
        """Exact durable receipt verifier; current source checks belong to coordinator."""
        import hashlib
        scope=current_scope()
        if hashlib.sha256(json.dumps(review,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=review_sha256:
            raise ValueError('acceptance exact review required')
        row=self._execute_native('SELECT * FROM closed_revision_acceptances WHERE acceptance_id=%s',(acceptance_reference,)).fetchone()
        if (not row or str(row['owner_id'])!=scope.owner_id or str(row['auth_session_id'])!=scope.session_id
            or row['task_id']!=review['task_id'] or row['review_sha256']!=review_sha256
            or review['owner_id']!=scope.owner_id or row['decision']!='approve' or row['extra_cost_acknowledged'] is not True
            or not self.auth_session_active()):raise TenantAccessDenied('current exact authenticated acceptance required')
        expected={'owner_id':scope.owner_id,'task_id':row['task_id'],'lineage_id':row['lineage_id'],'auth_session_id':scope.session_id,
            'review_sha256':review_sha256,'decision':'approve','extra_cost_acknowledged':True,
            'source':'private_authenticated_submission','installed':False,'dispatch_started':False}
        if json.loads(row['payload'])!=expected:raise ValueError('durable acceptance payload differs')
        if self._execute_native('SELECT 1 FROM closed_revision_acceptance_revocations WHERE acceptance_id=%s',(acceptance_reference,)).fetchone():
            raise TenantAccessDenied('private acceptance revoked')
        if self._execute_native('SELECT 1 FROM decision_drafts WHERE task_id=%s AND wait_id=%s AND revision=%s LIMIT 1',(row['task_id'],'revision-changes:'+row['lineage_id'],review_sha256)).fetchone():
            raise ValueError('review superseded by further changes')
        held=self.decision_review(row['task_id'],'revision-install:'+row['lineage_id'],review_sha256)
        if held!=review:raise ValueError('acceptance review is not retained')
        return True

    def revoke_private_revision_acceptance(self,task_id,*,acceptance_reference):
        with self._task_operation_guard('task.gate.decide'):
            row=self._execute_native('SELECT * FROM closed_revision_acceptances WHERE acceptance_id=%s AND task_id=%s',(acceptance_reference,task_id)).fetchone()
            if not row:raise KeyError(acceptance_reference)
            scope=current_scope()
            if not self.auth_session_active():raise TenantAccessDenied('active owner required')
            self._execute_native('INSERT INTO closed_revision_acceptance_revocations(acceptance_id,owner_id,auth_session_id,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (acceptance_reference,scope.owner_id,scope.session_id,time.time()), write=True)
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
            prior=self._execute_native('SELECT * FROM closed_revision_installations WHERE lineage_id=%s',(lineage_id,)).fetchone()
            if prior:
                if prior['plan_sha256']!=plan['plan_sha256'] or prior['acceptance_reference']!=acceptance_reference:
                    raise ValueError('conflicting installation intent replay')
                return plan|{'replayed':True}
            validate_acceptance(current['review'],review_sha256,acceptance_reference)
            scope=current_scope()
            self._execute_native('INSERT INTO closed_revision_installations(installation_id,lineage_id,owner_id,auth_session_id,task_id,review_sha256,plan_sha256,acceptance_reference,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (installation_id,lineage_id,scope.owner_id,scope.session_id,task_id,review_sha256,plan['plan_sha256'],acceptance_reference,json.dumps(plan['plan'],sort_keys=True),time.time()), write=True)
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            intent=self._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
            if not intent:raise KeyError(installation_id)
            plan=json.loads(intent['payload']);task=self.task(task_id);parent=plan['expected_parent']
            if task['generation']!=parent['generation'] or task['engine_session_id']!=parent['execution_session_id']:
                raise ValueError('installation parent generation/session changed')
            latest=self._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
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
            self._execute_native('INSERT INTO closed_revision_install_events(installation_id,owner_id,auth_session_id,sequence,state,event_sha256,previous_sha256,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (installation_id,scope.owner_id,scope.session_id,body['sequence'],state,event['event_sha256'],body['previous_sha256'],json.dumps(body,sort_keys=True),time.time()), write=True)
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            task=self.task(task_id);scope=current_scope()
            parent_current=(task['generation']==row['parent_generation'] and task['engine_session_id']==row['parent_execution_session_id'])
            root=session_root(Path(artifacts_base),scope,row['parent_execution_session_id'])
            writer=False
            if (binding is not None and binding[1]==row['parent_generation']
                and Path(binding[0].path)==root/'interview'/'stop-request.json'):
                binding[0].check(binding[1])
                from .pg_execution_fence import PostgresExecutionFence
                if isinstance(binding[0],PostgresExecutionFence):
                    writer=(binding[0].capability.scope==scope and binding[0].capability.task_id==task_id
                        and binding[0].capability.session_id==row['parent_execution_session_id']
                        and binding[0].active==1 and binding[0]._lease is not None)
                else:writer=binding[0].active==1 and binding[0]._writer_file is not None
            jobs=self._execute_native("SELECT job_id FROM worker_jobs WHERE task_id=%s AND state IN ('pending','running','paused','failed') ORDER BY job_id",(task_id,)).fetchall()
            attempts=self._execute_native("SELECT p.attempt_id FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s AND p.state IN ('reserved','dispatched','uncertain') ORDER BY p.attempt_id",(task_id,)).fetchall()
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            event=self._execute_native('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=%s ORDER BY sequence DESC LIMIT 1',(lineage_id,)).fetchone()
            if not event or event['state']!='staged' or event['event_sha256']!=expected_staged_event_sha256:
                raise ValueError('exact current staged journal required for review')
            payload=json.loads(event['payload'])
            if hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=event['event_sha256']:
                raise ValueError('staged event digest differs from payload')
            source=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(lineage_id,)).fetchone()
            intent=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s AND task_id=%s',(row['intent_id'],task_id)).fetchone()
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
            cap=WorkerCapability.capture(self,task_id);cap.validate(self)
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',(lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            latest=self._execute_native('SELECT * FROM closed_revision_promotion_events WHERE lineage_id=%s ORDER BY sequence DESC LIMIT 1',(lineage_id,)).fetchone()
            if not latest or latest['state']!='prepared' or latest['event_sha256']!=expected_previous_sha256:
                raise ValueError('exact prepared staging journal CAS required')
            review=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(lineage_id,)).fetchone()
            if not review:raise ValueError('retained upstream review snapshot required before staging')
            intent=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s AND task_id=%s',(row['intent_id'],task_id)).fetchone()
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',
                (lineage_id,task_id)).fetchone()
            if not row:raise KeyError(lineage_id)
            intent=self._execute_native('SELECT * FROM closed_revision_restart_intents WHERE intent_id=%s AND task_id=%s',
                (row['intent_id'],task_id)).fetchone()
            if not intent:raise KeyError(row['intent_id'])
            lineage=json.loads(row['payload']);held=json.loads(intent['payload'])
            from .revision_source_review import read_source_review_snapshot,source_review_payload
            review=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(lineage_id,)).fetchone()
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
            if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
                raise KeyError(task_id)
            capability=WorkerCapability.capture(self,task_id);capability.validate(self)
            row=self._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s AND task_id=%s',(lineage_id,task_id)).fetchone()
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
            prior=self._execute_native('SELECT * FROM closed_revision_source_reviews WHERE lineage_id=%s',(lineage_id,)).fetchone()
            if prior:
                if prior['fingerprint']!=snapshot['snapshot_sha256']:raise ValueError('source review snapshot changed; explicit new review required')
                return {'review_id':prior['review_id'],'snapshot_sha256':prior['fingerprint'],'state':'awaiting_review','accepted':False,'replayed':True}
            scope=current_scope();review_id=secrets.token_hex(16)
            self._execute_native('INSERT INTO closed_revision_source_reviews(review_id,lineage_id,owner_id,auth_session_id,fingerprint,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (review_id,lineage_id,scope.owner_id,scope.session_id,snapshot['snapshot_sha256'],json.dumps(snapshot['snapshot'],sort_keys=True),time.time()), write=True)
            self.audit(scope.owner_id,'gate.revision_source_review_retained',task_id,{'lineage_id':lineage_id,'review_id':review_id})
            return {'review_id':review_id,'snapshot_sha256':snapshot['snapshot_sha256'],'state':'awaiting_review','accepted':False,'replayed':False}

    def prepare_rerun(self,task_id,from_phase,policy='archive',actor='local user'):
        # Legacy generation reset leaves the immutable session binding stale.
        # Block internal callers too until a reviewed new-session copy exists.
        raise TenantAccessDenied('cloud rerun generation/session policy not implemented')

    def prepare_open_gate_continuation(self,task_id,phase,completed_job_id):
        """Consume one explicit generator open-gate policy after worker settlement.

        Owner/session claims and row lock are retained. Never crosses the
        interview boundary, resumes old work, or reuses a running worker.
        """
        from .phases import PHASE_BY_KEY,successor
        definition=PHASE_BY_KEY.get(phase);target=successor(phase) if definition else None
        if not definition or definition.executor!='generator' or not target or target.executor!='generator':return None
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():return None
        task=self.task(task_id);job=self.worker_job(completed_job_id)
        if not task or task.get('stop_requested_at') is not None:return None
        if not job or job['task_id']!=task_id or job['kind']!='phase:'+phase or job['state']!='done' or job['generation']!=task['generation']:return None
        states={p['phase_key']:p['state'] for p in task['phases']}
        gate=next((g for g in task['gates'] if g['after_phase']==phase),None)
        if task['active_phase']!=phase or states.get(phase)!='approved' or states.get(target.key)!='pending':return None
        if not gate or not gate['is_open'] or gate['state']!='bypassed':return None
        if self._execute_native("SELECT job_id FROM worker_jobs WHERE task_id=%s AND state IN ('pending','running','paused') LIMIT 1",(task_id,)).fetchone():return None
        from .model_routes import task_default_model
        model,_=task_default_model(self,task_id,target.generator_stage)
        from .model_routes import route_value
        route,_,name=model.partition(':');route_value(route,name)
        self.update_phase(task_id,target.key,'running',detail='Starting after explicit open-gate policy')
        queued=self.enqueue_worker(task_id,'phase:'+target.key,execution_options={'mode':'start','model_route':model})
        self.audit('policy','gate.continuation_queued',task_id,{'gate':gate['gate_key'],'completed_job_id':completed_job_id,'job_id':queued,'phase':target.key})
        return {'job_id':queued,'phase':target.key,'options':{'mode':'start','model_route':model}}

    def prepare_gate_dispatch(self,task_id,gate_key,action,*,actor,model=None,comment='',review_revision=None,**decision):
        """Commit the reviewed gate and its exact next generator job together."""
        from .phases import PHASE_BY_KEY,successor
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():
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
        return [dict(row) for row in self._execute_native("SELECT * FROM worker_jobs WHERE state IN ('pending','running','paused','failed') ORDER BY updated_at,job_id")]

    def task_worker_state(self,task_id,limit=20):
        """Read-only worker_jobs + provider_attempts state for one task's diagnostics.

        The Stop-settle gates and a parked interview both live in these two tables, and
        neither was visible in any export: a session parked in stop-requested left its
        job 'running' forever with no way to see that short of a database console. This
        is the same authenticated owner view as recoverable_worker_jobs, scoped to one
        task, newest first, with only the columns a diagnosis needs (no execution
        options, quotes, fingerprints or usage payloads)."""
        jobs=[dict(row) for row in self._execute_native(
            'SELECT job_id,kind,state,attempt,session_id,generation,created_at,updated_at FROM worker_jobs WHERE task_id=%s ORDER BY updated_at DESC LIMIT %s',(task_id,limit)).fetchall()]
        attempts=[dict(row) for row in self._execute_native(
            'SELECT p.attempt_id,p.job_id,p.state,p.expense_reserved,p.retail_reserved,p.expense_actual,p.retail_actual,p.created_at,p.updated_at FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s ORDER BY p.updated_at DESC LIMIT %s',(task_id,limit)).fetchall()]
        return {'worker_jobs':jobs,'provider_attempts':attempts}

    def worker_job(self,job_id):
        row=self._execute_native('SELECT * FROM worker_jobs WHERE job_id=%s',(job_id,)).fetchone()
        return dict(row) if row else None

    def claim_worker(self,job_id):
        from .worker_capability import WorkerCapability
        job=self.worker_job(job_id)
        if not job:return None
        capability=WorkerCapability.require_job(self,job)
        changed=self._execute_native("UPDATE worker_jobs SET state='running',attempt=attempt+1,updated_at=%s WHERE job_id=%s AND state IN ('pending','paused')",
                                (time.time(),job_id), write=True)
        return capability if changed.rowcount==1 else None

    def pause_interrupted_worker(self,job_id):
        # A live owner explicitly resumes this durable job; no global scan or
        # service-role worker guesses which user's authority to restore.
        self._execute_native("UPDATE worker_jobs SET state='paused',updated_at=%s WHERE job_id=%s AND state='running'",(time.time(),job_id), write=True)

    def reauthorize_worker(self,job_id):
        from .worker_capability import WorkerCapability
        # Serialize owner resume against worker claims and competing resumes.
        self._execute_native('SELECT job_id FROM worker_jobs WHERE job_id=%s FOR UPDATE',(job_id,)).fetchone()
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        capability=WorkerCapability.capture(self,job['task_id'])
        capability.validate(self)
        if job['state'] not in ('pending','paused','failed'):
            raise ValueError('only queued/interrupted/failed work can be explicitly resumed')
        if job['generation']!=capability.generation or job['session_id']!=capability.session_id:
            raise TenantAccessDenied('cannot resume another generation/session')
        self._execute_native("UPDATE worker_jobs SET auth_session_id=%s,state='paused',updated_at=%s WHERE job_id=%s AND state IN ('pending','paused','failed')",
                         (capability.scope.session_id,time.time(),job_id), write=True)

    def finish_stopped_worker(self,job_id,*,worker_quiescent):
        from .attempt_ledger import AdmissionDenied
        if worker_quiescent is not True:raise AdmissionDenied('worker quiescence unverified')
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        task=self.task(job['task_id'])
        if not task or task['stop_requested_at'] is None or str(job['auth_session_id'])!=current_scope().session_id:
            raise AdmissionDenied('original owner stop authority required')
        self._execute_native("UPDATE worker_jobs SET state='done',updated_at=%s WHERE job_id=%s AND state='running'",(time.time(),job_id), write=True)

    def finish_worker(self,job_id,failed=False):
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        if job['state']=='done':return
        from .worker_capability import WorkerCapability
        WorkerCapability.capture(self,job['task_id']).validate(self)
        self._execute_native("UPDATE worker_jobs SET state=%s,updated_at=%s WHERE job_id=%s AND state='running'",
                         ('failed' if failed else 'done',time.time(),job_id), write=True)

    @_transactional
    def staged_response_ledger_join(self,session_id,generation):
        """PR B reconciliation read: one session generation's engine-staged
        provider calls LEFT JOINed to their ledger attempts, in logical-call
        order. Read-only; the gateway classifies the rows."""
        rows=self._execute_native('SELECT r.*, a.state AS attempt_state, a.usage AS attempt_usage FROM staged_provider_responses r LEFT JOIN provider_attempts a ON a.attempt_id=r.attempt_id WHERE r.session_id=%s AND r.generation=%s ORDER BY r.execution_point,r.node,r.stage,r.item,r.ordinal',(session_id,generation)).fetchall()
        return [dict(r) for r in rows]

    def provider_attempt(self,attempt_id):
        row=self._execute_native('SELECT * FROM provider_attempts WHERE attempt_id=%s',(attempt_id,)).fetchone()
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
        self._execute_native("INSERT INTO expense_alert_outbox VALUES (%s,%s,%s::jsonb,'pending',0,%s,%s,%s) ON CONFLICT(alert_key) DO NOTHING",
            (alert.key,current_scope().owner_id,json.dumps(alert.payload),max_attempts,now,now), write=True)
        row=self._execute_native('SELECT * FROM expense_alert_outbox WHERE alert_key=%s',(alert.key,)).fetchone()
        if not row:raise AdmissionDenied('alert key unavailable for this owner')
        # Episode payload is first-occurrence evidence, never overwritten by retry.
        return dict(row)

    def claim_expense_alert(self,alert_key):
        row=self._execute_native('SELECT * FROM expense_alert_outbox WHERE alert_key=%s FOR UPDATE',(alert_key,)).fetchone()
        if not row:raise KeyError(alert_key)
        if row['state'] not in ('pending','retry_pending'):return None
        if row['attempts']>=row['max_attempts']:
            self._execute_native("UPDATE expense_alert_outbox SET state='operator_review',updated_at=%s WHERE alert_key=%s",(time.time(),alert_key),write=True)
            return None
        self._execute_native("UPDATE expense_alert_outbox SET state='sending',attempts=attempts+1,updated_at=%s WHERE alert_key=%s",(time.time(),alert_key), write=True)
        return dict(self._execute_native('SELECT * FROM expense_alert_outbox WHERE alert_key=%s',(alert_key,)).fetchone())

    def finish_expense_alert(self,alert_key,outcome):
        from .expense_alert import delivery_outcome
        from .attempt_ledger import AdmissionDenied
        row=self._execute_native('SELECT * FROM expense_alert_outbox WHERE alert_key=%s FOR UPDATE',(alert_key,)).fetchone()
        if not row:raise KeyError(alert_key)
        if row['state']!='sending':raise AdmissionDenied('claimed alert required')
        state=delivery_outcome(outcome=outcome,attempts=row['attempts'],max_attempts=row['max_attempts'])
        self._execute_native('UPDATE expense_alert_outbox SET state=%s,updated_at=%s WHERE alert_key=%s',(state,time.time(),alert_key), write=True)
        return state

    def recover_uncertain_expense_alert(self,alert_key):
        # A crash after SMTP acceptance may have sent the email. No blind retry.
        self._execute_native("UPDATE expense_alert_outbox SET state='operator_review',updated_at=%s WHERE alert_key=%s AND state='sending'",(time.time(),alert_key), write=True)

    def record_raw_provider_usage(self,attempt_id,raw_usage):
        from .attempt_ledger import AdmissionDenied
        import json,time
        if not isinstance(raw_usage,dict) or len(json.dumps(raw_usage,allow_nan=False).encode())>65536:
            raise AdmissionDenied('bounded raw provider usage object required')
        row=self._execute_native('SELECT * FROM provider_attempts WHERE attempt_id=%s FOR UPDATE',(attempt_id,)).fetchone()
        if not row or row['state'] not in ('dispatched','uncertain','settled'):
            raise AdmissionDenied('dispatched owner attempt required for raw evidence')
        if row['raw_usage'] is not None:
            if row['raw_usage']!=raw_usage:raise AdmissionDenied('conflicting raw usage replay')
            return dict(row)
        self._execute_native('UPDATE provider_attempts SET raw_usage=%s::jsonb,raw_usage_received_at=%s WHERE attempt_id=%s',
            (json.dumps(raw_usage,allow_nan=False),time.time(),attempt_id), write=True)
        return self.provider_attempt(attempt_id)

    def confirmed_expense_admission(self,job_id,expense_scale=None):
        # Returning a denial commits its durable episode/outbox. Raising here
        # would roll it back and lose the alert on every denied call.
        row=self._execute_native('SELECT public.requi_confirmed_expense_guard(%s,%s) AS reason',(job_id,expense_scale), write=True).fetchone()
        from .operational_alerts import emit
        reason=row['reason']
        if reason in ('session_expense_limit','platform_rolling_24h_expense_limit'):emit('tripwire_breach',reason)
        elif reason=='unknown_provider_outcome':emit('unresolved_accounting',reason)
        return reason

    def admit_provider_attempt(self,job_id,attempt_id,quote,digest,*,balance,bind_attempt,authorize,require_prepaid,launch_guard):
        """Parent-only reservation/binding/start transaction, after external IO."""
        self._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
        self.reserve_provider_attempt(job_id,attempt_id,quote,digest,require_prepaid=require_prepaid)
        bind_attempt(attempt_id);authorize()
        if 'estimate_policy' in quote:self.record_provider_balance_check(attempt_id,balance)
        if launch_guard:
            result=self.start_launch_provider_attempt(attempt_id)
            if not result['started']:
                self.transition_provider_attempt(attempt_id,'cancel',{'refusal':'service temporarily unavailable'})
                return result['reason']  # Commit the guard's durable episode before caller raises.
        else:self.transition_provider_attempt(attempt_id,'start',require_prepaid=require_prepaid)
        return 'allowed'

    def start_launch_provider_attempt(self,attempt_id):
        from .launch_admission_policy import validate_owner_limits,validate_launch_quote
        from .attempt_ledger import AdmissionDenied
        self._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
        row=self.provider_attempt(attempt_id)
        if not row:raise AdmissionDenied('owned attempt required')
        limits=self._execute_native('SELECT * FROM admission_limits WHERE owner_id=%s',(current_scope().owner_id,)).fetchone()
        validate_owner_limits(dict(limits) if limits else None);validate_launch_quote(row['quote'])
        reason=self.confirmed_expense_admission(row['job_id'],row['quote']['expense_units_per_usd'])
        if reason!='allowed':return {'started':False,'reason':reason}
        self.transition_provider_attempt(attempt_id,'start',require_prepaid=True)
        return {'started':True,'reason':'allowed'}

    def resolve_audit_event(self,audit_id,action,expected_attempt_state=None):
        from .audit_view import resolve
        return resolve(self,audit_id,action,expected_attempt_state)

    def transition_provider_attempt(self,attempt_id,action,usage=None,*,require_prepaid=False):
        from .attempt_ledger import transition
        self._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
        before=self.provider_attempt(attempt_id)
        result=transition(self,attempt_id,action,usage,require_prepaid=require_prepaid)
        if result.get('state')=='uncertain' and (not before or before['state']!='uncertain'):
            self.audit(str(result['owner_id']),'provider.attempt.uncertain',self.worker_job(result['job_id'])['task_id'],
                {'attempt_id':attempt_id,'job_id':result['job_id'],'outcome':'unknown','admission':'warning_only','automatic_retry':False})
        if result.get('state')=='uncertain':
            from .operational_alerts import emit
            emit('unresolved_accounting','provider_usage')
        return result

    def record_provider_balance_check(self,attempt_id,evidence):
        from .provider_balance import validate_balance
        row=self.provider_attempt(attempt_id)
        if not row or row['state']!='reserved':raise ValueError('reserved owner attempt required for balance evidence')
        checked=validate_balance(evidence,row['expense_reserved'],row['quote']['expense_units_per_usd'])
        self._execute_native('UPDATE provider_attempts SET provider_balance_evidence=%s::jsonb WHERE attempt_id=%s',(json.dumps(checked),attempt_id), write=True)
        return checked

    def task_child_cost(self,task_id):
        from .retail_ledger import child_task_cost
        return child_task_cost(self,task_id)

    def task_retail_totals(self,task_id):
        from .retail_ledger import task_totals
        return task_totals(self,task_id)

    def task_phase_credits(self,task_id):
        from .retail_ledger import task_phase_totals
        return task_phase_totals(self,task_id)

    def retail_compute_status(self,task_id):
        rows=self._execute_native("SELECT r.state,count(*) AS count FROM retail_compute_segments r JOIN worker_jobs w ON r.job_id=w.job_id WHERE w.task_id=%s GROUP BY r.state",(task_id,)).fetchall()
        return {r['state']:r['count'] for r in rows}

    def begin_retail_compute(self,key,job_id,phase,rates):
        from .retail_ledger import begin_compute
        return begin_compute(self,key,job_id,phase,rates)

    def close_failed_retail_compute(self,key):
        from .retail_ledger import close_failed_compute
        result=close_failed_compute(self,key)
        if result=='held':
            from .operational_alerts import emit
            emit('unresolved_accounting','compute_usage')
        return result

    def release_own_compute(self,confirm):
        from .retail_ledger import release_compute
        return release_compute(self,current_scope().owner_id,bool(confirm))

    def finish_retail_compute(self,key,deterministic_ms):
        from .retail_ledger import finish_compute
        result=finish_compute(self,key,deterministic_ms)
        if result is None:
            from .operational_alerts import emit
            emit('unresolved_accounting','compute_usage')
        return result

    def post_retail_contribution(self,**contribution):
        from .retail_ledger import post
        return post(self,**contribution)

    def retail_phase_totals(self,job_id):
        from .retail_ledger import phase_totals
        return phase_totals(self,job_id)

    def stop_hide_worker_task(self,job_id,*,confirm=False,actor='operator',hide=True):
        job=self.worker_job(job_id)
        if not job:raise KeyError(job_id)
        return self.stop_hide_task(job['task_id'],confirm=confirm,actor=actor,hide=hide)|{'job_id':job_id,'session_id':job['session_id']}

    def stop_hide_task(self,task_id,*,confirm=False,actor='owner',hide=True):
        """Confirmed own-task Stop+hide. Retain all work and uncertain accounting."""
        if confirm is not True:raise ValueError('explicit confirmation required')
        if type(hide) is not bool:raise ValueError('exact cleanup choice required')
        with self._task_operation_guard('task.delete' if hide else 'task.stop'):
            task=self._execute_native('SELECT * FROM tasks WHERE task_id=%s AND owner_id=%s FOR UPDATE',
                (task_id,current_scope().owner_id)).fetchone()
            if not task:raise KeyError(task_id)
            already=task['deleted_at'] is not None
            if not already:
                self.request_task_stop(task_id)
                if hide:self._execute_native('UPDATE tasks SET deleted_at=%s,updated_at=%s WHERE task_id=%s',
                    (time.time(),time.time(),task_id),write=True)
                self.audit(actor,'task.deleted' if hide else 'task.operator_stop_requested',task_id,
                    {'title':task['title'],'accounting_retained':True,'worker_exit_verified':False,'source_artifacts_deleted':False})
            return {'task_id':task_id,'hidden':already or hide,'stop_requested':True,'already_hidden':already,
                'worker_exit_verified':False,'accounting_settled':False,'accounting_retained':True,
                'source_artifacts_deleted':False,'accepted_calls_may_bill':True}

    def owner_active_jobs(self,*,limit=50,before=None):
        """Owner nonterminal workers and latest workers for active tasks; never proof of liveness."""
        import math
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit must be between 1 and 100')
        params=[current_scope().owner_id]
        cursor=''
        if before is not None:
            if (not isinstance(before,dict) or set(before)!={'updated_at','job_id'}
                or type(before['updated_at']) not in (int,float) or not math.isfinite(before['updated_at'])
                or before['updated_at']<0 or not isinstance(before['job_id'],str) or not 1<=len(before['job_id'])<=240):
                raise ValueError('exact bounded jobs cursor required')
            cursor=' AND (j.updated_at,j.job_id)<(%s,%s)'
            params.extend((before['updated_at'],before['job_id']))
        params.append(limit+1)
        rows=self._execute_native("SELECT j.job_id,j.task_id,j.session_id,j.generation,j.kind,j.state,j.created_at,j.updated_at,"
            "t.title,t.state AS task_state,t.generation AS task_generation,t.stop_requested_at,t.deleted_at,t.is_test, "
            "CASE WHEN j.state IN ('pending','running','paused') THEN 'nonterminal_worker' ELSE 'active_task_latest_job' END AS listed_for "
            "FROM worker_jobs j JOIN tasks t ON t.task_id=j.task_id AND t.owner_id=j.owner_id "
            "WHERE j.owner_id=%s AND t.deleted_at IS NULL AND (j.state IN ('pending','running','paused') OR "
            "(t.state IN ('running','waiting_gate','stop_requested') AND j.job_id=(SELECT latest.job_id FROM worker_jobs latest "
            "WHERE latest.task_id=t.task_id AND latest.owner_id=t.owner_id ORDER BY latest.generation DESC,latest.created_at DESC,latest.job_id DESC LIMIT 1)))"+cursor+
            " ORDER BY j.updated_at DESC,j.job_id DESC LIMIT %s",tuple(params)).fetchall()
        page=[dict(row)|{'quiescence_verified':False,'stale_verified':False} for row in rows[:limit]]
        truncated=len(rows)>limit
        return {'scope':'current_authorized_workspace','owner_id':current_scope().owner_id,'jobs':page,
            'truncated':truncated,'limit':limit,'next_before':{'updated_at':page[-1]['updated_at'],'job_id':page[-1]['job_id']} if truncated else None,
            'states_included':['pending','running','paused'],'active_task_latest_job_included':True,'mutations_available':False,'quiescence_verified':False}

    def operator_workspace_snapshot(self):
        """Read-only current-owner operations, never cross-tenant privilege."""
        owner=current_scope().owner_id
        account=self._execute_native('SELECT balance_units,units_per_credit,updated_at FROM credit_accounts WHERE owner_id=%s',(owner,)).fetchone()
        sessions=self._execute_native('SELECT session_id,task_id,generation FROM engine_sessions WHERE owner_id=%s ORDER BY session_id',(owner,)).fetchall()
        jobs=self._execute_native('SELECT job_id,task_id,session_id,generation,kind,state FROM worker_jobs WHERE owner_id=%s ORDER BY updated_at DESC LIMIT 100',(owner,)).fetchall()
        return {'scope':'current_authorized_workspace','owner_id':owner,
            'credits':dict(account) if account else None,'sessions':[dict(row) for row in sessions],
            'worker_jobs':[dict(row) for row in jobs],'credit_adjustment_available':False,
            'global_operator_access':False}

    def own_credit_balance(self,usd_per_credit):
        """Read-only: the signed-in owner's own prepaid balance. Public, so it runs inside a scoped transaction like every store operation."""
        from .account_balance import read
        return read(SimpleNamespace(execute=self._execute_native),current_scope().owner_id,usd_per_credit,marker='%s')

    def own_credit_history(self):
        """Read-only: every recorded change to the signed-in owner's credit balance."""
        from . import credit_history
        return credit_history.read(SimpleNamespace(execute=self._execute_native))

    def own_last_topup(self):
        """Read-only: the latest top-up or grant to the signed-in owner, or None."""
        from . import credit_history
        return credit_history.last_topup(SimpleNamespace(execute=self._execute_native))

    def own_ledger(self,usd_per_credit):
        """Read-only: the signed-in owner's credit balance and provider-call ledger, as text for the Debug dialog."""
        from .account_ledger import read
        return read(SimpleNamespace(execute=self._execute_native,pipeline=getattr(self._connection.get(),'pipeline',contextlib.nullcontext)),current_scope().owner_id,usd_per_credit,marker='%s')

    def _unsupported(self,*args,**kwargs):
        raise TenantAccessDenied('local authentication or quota provisioning unavailable on user connection')


# User-entry mutations and new work claims only. Nested accepted-worker writes,
# progress/usage evidence and completion remain available. Never gate settlement.
    # ---- PR B: artifact manifest authority (Postgres-only) ----

    def publish_task_artifact_bytes(self,task_id,phase_key,name,data,*,mime_type='application/json',writer_instance,validation_report=None):
        """Publish one owned byte member, with no filesystem authority."""
        import hashlib
        return self._publish_artifact_bytes(task_id,phase_key,name,data,
            ref='pg-artifact:'+hashlib.sha256(data).hexdigest(),mime_type=mime_type,
            writer_instance=writer_instance,validation_report=validation_report)

    def _publish_artifact_bytes(self,task_id,phase_key,name,data,*,ref,mime_type,writer_instance,validation_report=None):
        import hashlib
        if type(data) is not bytes or len(data)>64*1024*1024:raise ValueError('bounded artifact bytes required')

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
        self._execute_native('INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES (%s,%s,%s,%s) ON CONFLICT (owner_id,sha256) DO NOTHING',
            (scope.owner_id,digest,data,size), write=True)
        existing=self._execute_native('SELECT bytes FROM artifact_bytes WHERE owner_id=%s AND sha256=%s',(scope.owner_id,digest)).fetchone()
        if existing is None or bytes(existing['bytes'])!=data:
            raise ValueError('stored artifact bytes do not match the recorded content hash')
        self._execute_native(
            "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at,byte_size,sha256,publication_state,writer_instance,validator,valid,validation_error,validator_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'staged',%s,%s,%s,%s,%s)",
            (task_id,task['generation'],phase_key,name,str(ref),mime_type,now,size,digest,writer_instance,validator,valid,validation_error,validator_version), write=True)
        self._execute_native("UPDATE task_artifacts SET publication_state='superseded' WHERE task_id=%s AND generation=%s AND phase_key=%s AND name=%s AND publication_state='published'",
            (task_id,task['generation'],phase_key,name), write=True)
        changed=self._execute_native("UPDATE task_artifacts SET publication_state='published',published_at=%s WHERE task_id=%s AND generation=%s AND phase_key=%s AND name=%s AND publication_state='staged' AND sha256=%s",
            (now,task_id,task['generation'],phase_key,name,digest), write=True)
        if changed.rowcount!=1: raise ValueError('artifact publication CAS failed')
        return dict(self._execute_native("SELECT * FROM task_artifacts WHERE task_id=%s AND generation=%s AND phase_key=%s AND name=%s AND publication_state='published'",
            (task_id,task['generation'],phase_key,name)).fetchone())

    def manifest_artifacts(self,task_id,*,generation=None):
        """Manifest rows for a task (all publication states), newest generation first."""
        task=self.task(task_id)
        if not task: return []  # foreign or missing task: no existence oracle
        if generation is None: generation=task['generation']
        return [dict(r) for r in self._execute_native(
            'SELECT * FROM task_artifacts WHERE task_id=%s AND generation=%s ORDER BY phase_key,name,id',
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
            changed=self._execute_native(
                'INSERT INTO artifact_manifest_ready(owner_id,task_id,generation,revision_id,revision_hash,marked_at,writer_instance) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (task_id,generation) DO NOTHING',
                (scope.owner_id,task['task_id'],task['generation'],revision_id,revision_hash,now,writer_instance), write=True)
        else:
            changed=self._execute_native(
                'UPDATE artifact_manifest_ready SET revision_id=%s,revision_hash=%s,marked_at=%s,writer_instance=%s WHERE task_id=%s AND generation=%s AND revision_id=%s',
                (revision_id,revision_hash,now,writer_instance,task['task_id'],task['generation'],expected_revision_id), write=True)
        if changed.rowcount!=1: raise ValueError('readiness CAS failed: a concurrent producer published a revision')


    def publish_task_revision_bytes(self,task_id,artifacts,*,writer_instance):
        """Complete durable byte revision with the same atomic manifest CAS.

        No path is consulted. Existing file publisher is a temporary adapter to
        the exact same byte writer until producer sinks have been replaced.
        """
        import uuid
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
        task=self.task(task_id)
        if not isinstance(artifacts,list) or len({(a['phase_key'],a['name']) for a in artifacts})!=len(artifacts):raise ValueError('unique complete artifact revision required')
        marker=self._execute_native('SELECT revision_id FROM artifact_manifest_ready WHERE task_id=%s AND generation=%s',(task_id,task['generation'])).fetchone()
        published=[]
        for artifact in artifacts:
            data=artifact['data'];name=artifact['name'];phase=artifact['phase_key']
            import hashlib
            if type(data) is not bytes:raise ValueError('bounded artifact bytes required')
            ref='pg-artifact:'+hashlib.sha256(data).hexdigest()
            published.append(self._publish_artifact_bytes(task_id,phase,name,data,ref=ref,mime_type=artifact.get('mime_type','application/json'),
                writer_instance=writer_instance,validation_report=artifact.get('validation_report')))
        keep={(a['phase_key'],a['name']) for a in artifacts}
        for row in self._execute_native("SELECT phase_key,name FROM task_artifacts WHERE task_id=%s AND generation=%s AND publication_state='published'",(task_id,task['generation'])).fetchall():
            if (row['phase_key'],row['name']) not in keep:
                self._execute_native("UPDATE task_artifacts SET publication_state='superseded' WHERE task_id=%s AND generation=%s AND phase_key=%s AND name=%s AND publication_state='published'",(task_id,task['generation'],row['phase_key'],row['name']),write=True)
        members=self._execute_native("SELECT phase_key,name,sha256 FROM task_artifacts WHERE task_id=%s AND generation=%s AND publication_state='published'",(task_id,task['generation'])).fetchall()
        self._mark_revision_ready(task,uuid.uuid4().hex,self._revision_hash(members),marker['revision_id'] if marker else None,writer_instance,time.time())
        return published

    def manifest_snapshot(self,task_id,generation=None):
        """Marker + published members read as ONE statement = one consistent database
        snapshot: no check/read race with a concurrent publisher. Returns
        {'revision_id','revision_hash','rows'} or None when no ready marker exists.
        A marker whose hash no longer matches the members in the same snapshot is
        corruption - raise, never serve against it."""
        import json as _json
        row=self._execute_native(
            "WITH m AS (SELECT revision_id,revision_hash FROM artifact_manifest_ready WHERE task_id=%s AND generation=coalesce(%s,(SELECT generation FROM tasks WHERE task_id=%s))) SELECT m.revision_id,m.revision_hash,(SELECT json_agg(row_to_json(t)) FROM (SELECT * FROM task_artifacts WHERE task_id=%s AND generation=coalesce(%s,(SELECT generation FROM tasks WHERE task_id=%s)) AND publication_state='published' ORDER BY phase_key,name,id) t) AS members FROM m",
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
        marker=self._execute_native('SELECT revision_id,revision_hash FROM artifact_manifest_ready WHERE task_id=%s AND generation=%s',
            (task_id,generation)).fetchone()
        if marker is None:return None
        members=self._execute_native("SELECT phase_key,name,sha256 FROM task_artifacts WHERE task_id=%s AND generation=%s AND publication_state='published'",
            (task_id,generation)).fetchall()
        if self._revision_hash(members)!=marker['revision_hash']:
            raise ValueError('readiness marker does not match the published manifest revision')
        return marker['revision_id'],marker['revision_hash']

    def producer_result(self,job_id):
        """Durable produced-awaiting-publication state for a worker job, or None."""
        row=self._execute_native('SELECT * FROM producer_results WHERE job_id=%s',(job_id,)).fetchone()
        return dict(row) if row else None

    def record_producer_result(self,job_id,task_id,generation,session_root,content_hash):
        """Persist that the paid producer completed: its bytes are at session_root
        (pinned by content_hash) and only publication remains."""
        import time as _time
        self._execute_native(
            'INSERT INTO producer_results(owner_id,job_id,task_id,generation,session_root,content_hash,produced_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (job_id) DO UPDATE SET task_id=EXCLUDED.task_id,generation=EXCLUDED.generation,session_root=EXCLUDED.session_root,content_hash=EXCLUDED.content_hash,produced_at=EXCLUDED.produced_at,published_at=NULL',
            (current_scope().owner_id,job_id,task_id,generation,session_root,content_hash,_time.time()), write=True)

    def producer_paid_evidence(self,job_id):
        """Count of provider attempt rows recorded for a job: evidence that paid work
        may already have happened when no produced marker exists."""
        row=self._execute_native('SELECT count(*) AS n FROM provider_attempts WHERE job_id=%s',(job_id,)).fetchone()
        return row['n'] if row else 0

    def mark_producer_published(self,job_id):
        import time as _time
        changed=self._execute_native('UPDATE producer_results SET published_at=%s WHERE job_id=%s AND published_at IS NULL',
            (_time.time(),job_id), write=True)
        if changed.rowcount!=1: raise ValueError('producer result was already marked published')

    def create_final_package(self,task_id):
        """Task row lock pins approval, generation and manifest through export+insert."""
        from .cloud_package import build
        from .final_package import PackageBlocked
        import hashlib
        if not self._execute_native('SELECT task_id FROM tasks WHERE task_id=%s AND deleted_at IS NULL FOR UPDATE',(task_id,)).fetchone():raise KeyError(task_id)
        task=self.task(task_id)
        if task['state']!='done' or task.get('stop_requested_at') is not None or any(p['state'] not in ('approved','done') for p in task['phases']):raise PackageBlocked('Final approval is required')
        if self._execute_native("SELECT 1 FROM worker_jobs WHERE task_id=%s AND state IN ('pending','running') LIMIT 1",(task_id,)).fetchone():raise PackageBlocked('Worker must finish before export')
        snapshot=self.manifest_snapshot(task_id,task['generation'])
        if snapshot is None:raise PackageBlocked('Published final revision is required')
        owner=current_scope().owner_id
        identity,raw,contents=build(self,task,snapshot,owner)
        if self.manifest_snapshot(task_id,task['generation'])!=snapshot:raise PackageBlocked('Final revision changed')
        self._execute_native('INSERT INTO final_packages(owner_id,task_id,generation,package_id,execution_session_id,revision_id,revision_hash,payload,sha256,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (owner,task_id,task['generation'],identity,task['engine_session_id'],snapshot['revision_id'],snapshot['revision_hash'],raw,hashlib.sha256(raw).hexdigest(),time.time()),write=True)
        self.final_package(task_id,task['generation'],identity)
        return {'package_id':identity,'generation':task['generation'],'contents':contents}

    def final_package(self,task_id,generation,package_id):
        from .cloud_package import verify
        from .final_package import PackageBlocked
        import hashlib
        row=self._execute_native('SELECT * FROM final_packages WHERE task_id=%s AND generation=%s AND package_id=%s',(task_id,generation,package_id)).fetchone()
        if row is None:raise KeyError(package_id)
        raw=bytes(row['payload'])
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise PackageBlocked('Stored package checksum differs')
        verify(raw,package_id=package_id,owner_id=str(row['owner_id']),task_id=task_id,generation=generation,revision_id=row['revision_id'],revision_hash=row['revision_hash'],session_id=row['execution_session_id'])
        return raw

    def read_artifact_bytes_sha(self,sha256):
        """Read immutable bytes by content hash - one exact published revision, safe to
        combine with a previously taken manifest snapshot under concurrent republish."""
        row=self._execute_native('SELECT bytes FROM artifact_bytes WHERE owner_id=%s AND sha256=%s',(current_scope().owner_id,sha256)).fetchone()
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
        row=self._execute_native("SELECT * FROM task_artifacts WHERE task_id=%s AND generation=%s AND phase_key=%s AND name=%s AND publication_state='published'",
            (task_id,generation,phase_key,name)).fetchone()
        if row is None: raise KeyError(f'no published artifact {phase_key}/{name}')
        stored=self._execute_native('SELECT bytes FROM artifact_bytes WHERE owner_id=%s AND sha256=%s',
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
 'decide_gate':'task.gate.decide','prepare_gate_dispatch':'task.gate.decide','prepare_open_gate_continuation':'task.gate.decide',
 'add_review_comment':'task.decision.comment','save_decision_draft':'task.decision.draft',
 'set_model_route':'task.model_route','set_model_default':'task.model_route',
 'begin_interview_start':'task.start','enqueue_worker':'task.start',
 'claim_worker':'worker.claim','reauthorize_worker':'worker.claim',
 'prepare_phase_retry':'task.retry','claim_failed_needs_retry':'task.retry',
 'claim_failed_phase_retry':'task.retry','prepare_rerun':'task.rerun',
 'upsert_feedback':'task.edit','update_feedback':'task.edit','delete_feedback':'task.edit'}

# Public surface is reviewed explicitly. A coverage test rejects an unregistered
# public method, so adding one cannot silently miss scoped transaction wrapping.
_LOCAL_AUTH = {'create_oauth_state','consume_oauth_state','upsert_user','create_basic_user',
 'basic_user','create_session','session','rotate_session','delete_session','set_user_quota'}
PG_OPERATIONS = (
    'admit_provider_attempt',
    'activate_private_revision_installation',
    'add_artifact',
    'add_event',
    'add_review_comment',
    'add_task_artifact',
    'add_usage',
    'add_user_usage',
    'append_closed_revision_preparation_event',
    'append_private_installation_event',
    'artifacts',
    'attribute_run',
    'audit',
    'audit_entries',
    'auth_session_active','authority_snapshot',
    'begin_interview_start',
    'begin_retail_compute',
    'bind_interview_schema',
    'claim_expense_alert',
    'claim_failed_needs_retry',
    'claim_failed_phase_retry',
    'claim_next_phase',
    'claim_worker',
    'close_failed_retail_compute',
    'configure_simulation',
    'confirm_task_stopped',
    'confirm_child_session_quiescent',
    'confirmed_expense_admission',
    'create_link',
    'create_task',
    'create_final_package',
    'final_package',
    'deactivate_link',
    'decide_gate',
    'decision_draft',
    'decision_review',
    'delete_feedback',
    'delete_task',
    'engine_session',
    'enqueue_expense_alert',
    'enqueue_worker',
    'ensure_waiting_input_event',
    'events',
    'failure_diagnostics',
    'finish_expense_alert',
    'finish_retail_compute',
    'finish_stopped_worker',
    'finish_worker',
    'ingest_progress',
    'ingest_interview_progress_page',
    'inspect_revision_install_safety',
    'installed_execution_anchor',
    'interview_schema_binding',
    'link',
    'list_feedback',
    'list_tasks',
    'list_test_tasks',
    'purge_test_task',
    'test_task_expense',
    'test_task_breakdown',
    'manifest_artifacts',
    'manifest_ready',
    'manifest_revision',
    'manifest_snapshot',
    'mark_producer_published',
    'operator_workspace_snapshot','owner_active_jobs','stop_hide_worker_task','stop_hide_task',
    'own_credit_balance',
    'own_credit_history',
    'own_last_topup',
    'own_ledger',
    'pause_interrupted_worker',
    'post_retail_contribution',
    'prepare_gate_dispatch',
    'prepare_open_gate_continuation',
    'prepare_next_phase',
    'prepare_phase_retry',
    'prepare_private_revision_changes',
    'prepare_rerun',
    'prepare_revision_staging',
    'private_approved_continuation_basis',
    'producer_paid_evidence',
    'producer_result',
    'progress_cursor',
    'provider_attempt',
    'publish_task_artifact_bytes',
    'publish_task_revision_bytes',
    'quota_allows',
    'read_artifact_bytes',
    'read_artifact_bytes_sha',
    'read_promoted_installation_evidence',
    'reauthorize_worker',
    'reconcile_completed_interview',
    'reconcile_private_revision_installation',
    'record_failure_diagnostics',
    'record_private_revision_acceptance',
    'record_producer_result',
    'record_provider_balance_check',
    'record_raw_provider_usage',
    'record_validated_revision_staging',
    'recover_uncertain_expense_alert',
    'recoverable_worker_jobs',
    'release_own_compute','resolve_audit_event',
    'rename_task',
    'request_task_stop',
    'rerun_prerequisite',
    'reserve_provider_attempt',
    'retail_compute_status',
    'retail_phase_totals',
    'retain_closed_revision_lineage_plan',
    'retain_closed_revision_restart_intent',
    'retain_decision_review',
    'retain_private_installation_intent',
    'retain_revision_installation_review',
    'retain_revision_source_review',
    'review_comments',
    'revoke_private_revision_acceptance',
    'run',
    'save_decision_draft',
    'session_binding',
    'set_engine_session',
    'set_gate_open',
    'set_model_default',
    'set_model_route',
    'set_state',
    'set_submission',
    'staged_response_ledger_join',
    'start_launch_provider_attempt',
    'task',
    'task_for_engine_session',
    'task_artifacts',
    'task_phase_credits',
    'task_child_cost',
    'task_retail_totals',
    'task_snapshot',
    'task_worker_state',
    'tombstone_stopped_task',
    'total_spend',
    'transition_provider_attempt',
    'update_feedback',
    'update_phase',
    'upsert_feedback',
    'user_quota',
    'verify_private_revision_acceptance',
    'wait_at_gate',
    'worker_job',
)
for _name in PG_OPERATIONS:
    setattr(PostgresStore,_name,_transactional(getattr(PostgresStore,_name)))
for _name in _LOCAL_AUTH:
    setattr(PostgresStore,_name,_transactional(PostgresStore._unsupported))
