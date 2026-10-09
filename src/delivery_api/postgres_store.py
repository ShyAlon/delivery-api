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
import threading
import time
import sqlite3
from .store import SQLiteStore, PHASES
from vmodel_contracts import RunState
from .postgres_scope import ScopedPostgres
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
    def __init__(self, state): self.state = state

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
        returning = bool(re.match(r'INSERT INTO (events|audit_log|review_comments)\b',sql))
        if returning: sql += ' RETURNING id'
        try:
            return _Cursor(connection.execute(sql,parameters),returning=returning)
        except Exception as exc:
            import psycopg
            if isinstance(exc,psycopg.errors.UniqueViolation):
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise

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
        with self._postgres.transaction() as connection:
            connection.execute('SAVEPOINT operation_start')
            token = self._connection.set(connection)
            try: return method(self,*args,**kwargs)
            finally: self._connection.reset(token)
    return call


class PostgresStore(SQLiteStore):
    def __init__(self,dsn):
        self._postgres = ScopedPostgres(dsn)
        self._connection = ContextVar('postgres_operation',default=None)
        self._db = _Connection(self._connection)
        self._lock = threading.RLock()

    def auth_session_active(self):
        return bool(self._db.execute('SELECT public.requi_session_active() AS active').fetchone()['active'])

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

    def list_tasks(self):
        rows=self._db.execute('SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY updated_at DESC').fetchall()
        return [self._task_record(row) for row in rows]

    def task(self,task_id):
        row=self._db.execute('SELECT * FROM tasks WHERE task_id=? AND deleted_at IS NULL',(task_id,)).fetchone()
        return self._task_record(row) if row else None

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
        return self._task_record(self._db.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone())

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

    def _task_record(self,row):
        result=super()._task_record(row)
        result['owner_id']=str(result['owner_id'])
        return result

    def enqueue_worker(self,task_id,kind,*,execution_options=None):
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

    def transition_provider_attempt(self,attempt_id,action,usage=None,*,require_prepaid=False):
        from .attempt_ledger import transition
        return transition(self,attempt_id,action,usage,require_prepaid=require_prepaid)

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

    def _unsupported(self,*args,**kwargs):
        raise TenantAccessDenied('local authentication or quota provisioning unavailable on user connection')


# Only explicit public operations wrap, not helpers. A newly added inherited
# method also gets a transaction by default; no unsafe unscoped SQL pathway.
_LOCAL_AUTH = {'create_oauth_state','consume_oauth_state','upsert_user','create_basic_user',
 'basic_user','create_session','session','rotate_session','delete_session','set_user_quota'}
for _name in set(vars(SQLiteStore)) | set(vars(PostgresStore)):
    if _name.startswith('_'): continue
    _method=getattr(PostgresStore,_name,None)
    if callable(_method):
        setattr(PostgresStore,_name,_transactional(PostgresStore._unsupported if _name in _LOCAL_AUTH else _method))
