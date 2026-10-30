"""Job-fenced generator checkpoints on the existing tenant PG checkpoint tables.

A job capability is captured at claim, never from a path. Each saver operation
uses one claimed shared-pool transaction, locks task+job and checks generation,
auth session and running attempt. Stop/finish serialize with checkpoint writes.
Generator namespaces cannot overlap interview root or each other.
"""
from contextlib import contextmanager
import re
from langgraph.checkpoint.postgres import PostgresSaver
from .tenancy import tenant_context,current_scope,TenantAccessDenied

class GeneratorCheckpoint(PostgresSaver):
    def __init__(self,store,capability,job_id,namespace,*,logical_thread_id=None,expected_attempt=None,writable=True):
        from spec_generator.graph import checkpoint_serde
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9:_-]{0,199}',namespace):
            raise ValueError('bounded generator namespace required')
        from .historical_checkpoint import HistoricalCheckpointCapability
        if isinstance(capability,HistoricalCheckpointCapability) and (writable or job_id is not None):raise TenantAccessDenied('historical checkpoint is read-only')
        capability.validate(store)
        job=store.worker_job(job_id) if job_id is not None else None
        if job_id is not None and (not job or job['state']!='running' or job['task_id']!=capability.task_id or job['generation']!=capability.generation):
            raise TenantAccessDenied('claimed generator job required')
        if job_id is None and writable:raise TenantAccessDenied('checkpoint writer requires a claimed job')
        self.writable=writable
        if expected_attempt is not None and (not job or job['attempt']!=expected_attempt):raise TenantAccessDenied('generator attempt changed')
        self.store=store;self.capability=capability;self.job_id=job_id;self.attempt=job['attempt'] if job else None
        self.namespace='generator:'+namespace
        self.logical_thread_id=logical_thread_id or capability.session_id
        if logical_thread_id is not None and not namespace.startswith('private:'):raise ValueError('logical staging requires private namespace')
        super().__init__(store._postgres._pool,serde=checkpoint_serde())

    @contextmanager
    def _transaction(self):
        current=self.store._connection.get()
        if current is not None:
            yield current
        else:
            with self.store._postgres.transaction() as c:yield c

    @contextmanager
    def _cursor(self,*,pipeline=False):
        cap=self.capability
        # LangGraph checkpoint worker threads may not inherit request ContextVars;
        # only this prevalidated immutable capability restores their identity.
        try:scope=current_scope()
        except TenantAccessDenied:scope=None
        if scope is not None and scope!=cap.scope:raise TenantAccessDenied('checkpoint caller differs')
        with tenant_context(cap.scope),self._transaction() as c:
            # Read capabilities use MVCC and never hold a task lock across an
            # HTTP request which may call the engine on another connection.
            task=c.execute('SELECT generation,stop_requested_at FROM tasks WHERE task_id=%s'+(' FOR UPDATE' if self.writable else ''),(cap.task_id,)).fetchone()
            job=c.execute('SELECT state,attempt,auth_session_id,generation FROM worker_jobs WHERE job_id=%s FOR UPDATE',(self.job_id,)).fetchone()
            from .historical_checkpoint import HistoricalCheckpointCapability
            historical=isinstance(cap,HistoricalCheckpointCapability)
            if (not task or (task['generation']!=cap.generation and not historical) or (task['stop_requested_at'] is not None and not historical)
                or (self.job_id is not None and (not job or job['state']!='running' or job['attempt']!=self.attempt
                    or job['generation']!=cap.generation or str(job['auth_session_id'])!=cap.scope.session_id))):
                raise TenantAccessDenied('generator checkpoint authority lost')
            token=self.store._connection.set(c)
            try:
                cap.validate(self.store)
                with self.lock,c.cursor(binary=True) as cursor:yield cursor
            finally:self.store._connection.reset(token)

    def _config(self,config):
        if config is None:return None
        value=dict(config);cfg=dict(value.get('configurable',{}))
        if cfg.get('thread_id')!=self.logical_thread_id:raise TenantAccessDenied('checkpoint thread differs')
        cfg['thread_id']=self.capability.session_id
        ns=cfg.get('checkpoint_ns','')
        if ns not in ('',self.namespace):raise TenantAccessDenied('checkpoint namespace differs')
        cfg['checkpoint_ns']=self.namespace;value['configurable']=cfg
        return value

    def _out_config(self,config):
        if config is None:return None
        value=dict(config);cfg=dict(value['configurable'])
        cfg['thread_id']=self.logical_thread_id;cfg['checkpoint_ns']=''
        value['configurable']=cfg;return value
    def _out_tuple(self,row):
        if row is None:return None
        return row._replace(config=self._out_config(row.config),parent_config=self._out_config(row.parent_config))
    def get_tuple(self,config):return self._out_tuple(super().get_tuple(self._config(config)))
    def put(self,config,*args,**kwargs):
        if not self.writable:raise PermissionError('read-only checkpoint capability')
        return self._out_config(super().put(self._config(config),*args,**kwargs))
    def put_writes(self,config,*args,**kwargs):
        if not self.writable:raise PermissionError('read-only checkpoint capability')
        return super().put_writes(self._config(config),*args,**kwargs)
    def list(self,config,**kwargs):
        if config is None:raise ValueError('explicit session checkpoint list required')
        if kwargs.get('before') is not None:kwargs['before']=self._config(kwargs['before'])
        return (self._out_tuple(row) for row in super().list(self._config(config),**kwargs))
    def setup(self):
        with self._cursor() as c:
            row=c.execute('SELECT max(v) AS v FROM checkpoint_migrations').fetchone()
            if not row or row['v']<len(self.MIGRATIONS)-1:raise RuntimeError('checkpoint migration required; no runtime DDL')
    def get_delta_channel_history(self,*,config,channels):
        from langgraph.checkpoint.base import get_checkpoint_id
        if get_checkpoint_id(config) is None:
            row=self.get_tuple(config)
            if row is None:return {ch:{'writes':[]} for ch in channels}
            config=row.config
        return super().get_delta_channel_history(config=self._config(config),channels=channels)
    def delete_thread(self,*args,**kwargs):raise PermissionError('checkpoint deletion is not runtime authority')

class ChildGeneratorCheckpoint(GeneratorCheckpoint):
    """Same namespace contract, fenced by the child's already-claimed DB lease.

    Also checks the original running worker attempt. A valid session lease alone
    cannot keep a finished/cancelled/retried worker alive.
    """
    def __init__(self,store,claim,binding,namespace,*,expected_attempt=None):
        from types import SimpleNamespace
        from spec_generator.graph import checkpoint_serde
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9:_-]{0,199}',namespace):
            raise ValueError('bounded generator namespace required')
        self.store=store;self.claim=claim;self.binding=binding;self.writable=True
        self.capability=SimpleNamespace(session_id=binding.session_id)
        self.logical_thread_id=binding.session_id
        self.namespace='generator:'+namespace
        PostgresSaver.__init__(self,store.pool,serde=checkpoint_serde())
        with store._borrow() as c,c.transaction():
            row=c.execute('SELECT attempt FROM worker_jobs WHERE job_id=%s AND state=%s',(binding.job_id,'running')).fetchone()
            if row is None:raise TenantAccessDenied('running child worker required')
            if expected_attempt is not None and row['attempt']!=expected_attempt:raise TenantAccessDenied('child generator attempt changed')
            self.attempt=row['attempt']

    @contextmanager
    def _cursor(self,*,pipeline=False):
        import time
        from .tenancy import TenantAccessDenied
        b=self.binding;claim=self.claim
        with self._child_transaction() as c:
            task=c.execute('SELECT generation,stop_requested_at FROM tasks WHERE task_id=%s FOR UPDATE',(b.task_id,)).fetchone()
            job=c.execute('SELECT state,attempt,auth_session_id,generation FROM worker_jobs WHERE job_id=%s FOR UPDATE',(b.job_id,)).fetchone()
            lease=c.execute('SELECT generation,lease_epoch,lease_expires_at,placement_instance,control FROM engine_sessions WHERE session_id=%s FOR UPDATE',(b.session_id,)).fetchone()
            if (not task or task['generation']!=b.generation or task['stop_requested_at'] is not None
                or not job or job['state']!='running' or job['attempt']!=self.attempt
                or job['generation']!=b.generation or str(job['auth_session_id'])!=str(b.auth_session_id)
                or not lease or lease['generation']!=claim.generation or lease['lease_epoch']!=claim.epoch
                or lease['lease_expires_at']<=time.time() or lease['placement_instance']!=claim.instance
                or (lease['control'] or {}).get('stopped')):
                raise TenantAccessDenied('generator child checkpoint authority lost')
            if b.generation>1:self._validate_installed(c)
            with self.lock,c.cursor(binary=True) as cur:yield cur

    def _validate_installed(self,conn):
        import hashlib,json
        from vmodel_contracts.execution_anchor import ExecutionAnchor
        b=self.binding
        row=conn.execute('SELECT * FROM installed_execution_bindings WHERE session_id=%s',(b.session_id,)).fetchone()
        if not row or row['anchor_sha256']!=b.execution_anchor_sha256:raise TenantAccessDenied('child installed binding changed')
        intent=conn.execute('SELECT * FROM closed_revision_installations WHERE installation_id=%s',(row['installation_id'],)).fetchone()
        latest=conn.execute('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(row['installation_id'],)).fetchone()
        if not intent or not latest or latest['state']!='installed':raise TenantAccessDenied('child installed journal unavailable')
        payload=json.loads(latest['payload']);plan=json.loads(intent['payload'])
        anchor=ExecutionAnchor(str(row['owner_id']),row['task_id'],row['session_id'],row['semantic_session_id'],row['generation'],row['lineage_sha256'],row['installation_id'])
        if (anchor.owner_id!=str(b.owner_id) or anchor.task_id!=b.task_id or anchor.execution_session_id!=b.session_id or anchor.generation!=b.generation
            or hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=latest['event_sha256']
            or hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=intent['plan_sha256']
            or payload['plan_sha256']!=intent['plan_sha256'] or payload['evidence']['execution_session_id']!=b.session_id
            or payload['evidence']['anchor_sha256']!=row['anchor_sha256'] or plan['destination_anchor']!=anchor.document()):
            raise TenantAccessDenied('child installed evidence differs')

    @contextmanager
    def _child_transaction(self):
        current=_child_connection.get()
        if current is not None:
            if current[0] is not self.store:raise TenantAccessDenied('child transaction store differs')
            yield current[1]
        else:
            with self.store._borrow() as conn,conn.transaction():
                token=_child_connection.set((self.store,conn))
                try:yield conn
                finally:_child_connection.reset(token)

from contextvars import ContextVar
_child_connection=ContextVar('generator_child_transaction',default=None)

