"""Captured PG execution publication fence, with no marker-file authority."""
from contextlib import contextmanager
import threading
from .tenancy import tenant_context,current_scope,TenantAccessDenied

class PostgresExecutionFence:
    def __init__(self,store,capability,*,root):
        from pathlib import Path
        from .tenancy import session_root
        expected=session_root(Path(root).parents[3],capability.scope,capability.session_id)
        if Path(root)!=expected:raise ValueError('owned canonical execution root required')
        capability.validate(store)
        self.store=store;self.capability=capability;self.path=Path(root)/'interview/stop-request.json'
        self.generation=capability.generation;self._execution_anchor=capability.execution_anchor
        self.lock=threading.RLock();self.active=0;self._lease=None

    def check(self,generation):
        from spec_generator.stop_fence import StopRequested
        try:scope=current_scope()
        except TenantAccessDenied:scope=None
        if scope is not None and scope!=self.capability.scope:raise TenantAccessDenied('execution fence caller differs')
        with tenant_context(self.capability.scope):
            task=self.store.task(self.capability.task_id)
            if not self.store.auth_session_active():raise TenantAccessDenied('execution fence owner revoked')
            if not task:raise TenantAccessDenied('execution fence task unavailable')
            if generation!=self.generation or task.get('stop_requested_at') is not None or task['generation']!=self.generation:raise StopRequested('session stop requested')
            self.capability.validate(self.store)

    @contextmanager
    def marker_guard(self):
        # Same task row serializes publication with Stop and generation CAS.
        try:scope=current_scope()
        except TenantAccessDenied:scope=None
        if scope is not None and scope!=self.capability.scope:raise TenantAccessDenied('execution fence caller differs')
        with tenant_context(self.capability.scope),self.store._task_operation_guard('task.rerun'):
            self.store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(self.capability.task_id,)).fetchone()
            self.check(self.generation);yield

    @property
    def requested(self):
        with tenant_context(self.capability.scope):
            task=self.store.task(self.capability.task_id)
            if not task or not self.store.auth_session_active():raise TenantAccessDenied('execution fence owner unavailable')
            return task.get('stop_requested_at') is not None

    @property
    def state(self):
        return ('STOPPED' if self.writer_quiescent() else 'STOP_REQUESTED') if self.requested else None

    def writer_quiescent(self):
        if self.active:return False
        key='pg-execution-writer:'+self.capability.scope.owner_id+':'+self.capability.session_id
        with self.store._postgres.transaction() as conn:
            held=conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()['held']
            if held:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
            return held

    def confirm_quiescent_after_recovery(self,*,no_live_worker):
        from spec_generator.stop_fence import StopRequested
        if no_live_worker is not True or not self.writer_quiescent():raise StopRequested('worker quiescence unverified')
        return self.state

    def _writer_enter(self):
        with self.lock:
            if self.active==0:
                key='pg-execution-writer:'+self.capability.scope.owner_id+':'+self.capability.session_id
                context=self.store._postgres.transaction();lease=context.__enter__()
                try:
                    if not lease.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()['held']:
                        raise BlockingIOError('execution writer remains active')
                except BaseException:
                    import sys
                    context.__exit__(*sys.exc_info());raise
                self._lease=(context,lease,key)
            self.active+=1

    def _writer_exit(self):
        with self.lock:
            self.active-=1
            if self.active==0 and self._lease is not None:
                context,lease,key=self._lease;self._lease=None
                try:lease.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
                finally:context.__exit__(None,None,None)

    @contextmanager
    def running(self):
        from spec_generator.stop_fence import execution_fence,_publish,_current
        self.check(self.generation);self._writer_enter()
        try:
            def publish(call):
                with self.marker_guard():return call()
            p=_publish.set(publish);c=_current.set(self)
            try:
                with execution_fence(lambda:self.check(self.generation)):yield self.generation
            finally:_current.reset(c);_publish.reset(p)
        finally:self._writer_exit()


class PostgresControlFence:
    """Owner-scoped Stop control, never a graph publication capability.

    It accepts exactly the captured execution generation or that execution's
    durable stopped successor. It cannot bless another installed generation.
    """
    def __init__(self,store,capability):
        self.store=store;self.capability=capability;self.generation=capability.generation
        self._validate()

    def _validate(self):
        cap=self.capability
        if current_scope()!=cap.scope or not self.store.auth_session_active():raise TenantAccessDenied('control owner revoked or changed')
        task=self.store.task(cap.task_id);binding=self.store.session_binding(cap.session_id)
        if (not task or not binding or binding['task_id']!=cap.task_id or binding['generation']!=cap.generation
            or str(binding['owner_id'])!=cap.scope.owner_id or task['engine_session_id']!=cap.session_id
            or task['generation'] not in (cap.generation,cap.generation+1)
            or (task['generation']==cap.generation+1 and task.get('stop_requested_at') is None)):
            raise TenantAccessDenied('captured execution control binding changed')
        if cap.generation>1 and self.store.installed_execution_anchor(cap.session_id)!={'anchor':cap.execution_anchor,'anchor_sha256':cap.anchor_sha256}:
            raise TenantAccessDenied('installed control binding changed')
        return task

    @contextmanager
    def marker_guard(self):
        with self.store._task_operation_guard('task.stop'):
            self.store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(self.capability.task_id,)).fetchone()
            self._validate();yield

    @property
    def requested(self):return self._validate().get('stop_requested_at') is not None

    def writer_quiescent(self):
        self._validate()
        key='pg-execution-writer:'+self.capability.scope.owner_id+':'+self.capability.session_id
        with self.store._postgres.transaction() as conn:
            held=conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()['held']
            if held:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
            return held

    @property
    def state(self):
        return ('STOPPED' if self.writer_quiescent() else 'STOP_REQUESTED') if self.requested else None

    def request_stop(self):
        with self.marker_guard():self.store.request_task_stop(self.capability.task_id)
        return self.state

    def confirm_quiescent_after_recovery(self,*,no_live_worker):
        from spec_generator.stop_fence import StopRequested
        if no_live_worker is not True or not self.writer_quiescent():raise StopRequested('worker quiescence unverified')
        if not self.requested:raise ValueError('durable stop request required')
        return 'STOPPED'


class ChildPostgresExecutionFence:
    """Child writer lifetime plus short PG publication transactions, no files."""
    def __init__(self,documents,*,root):
        from pathlib import Path
        self.documents=documents;self.saver=documents.saver;self.generation=documents.capability.generation
        self.path=Path(root)/'interview/stop-request.json';self.lock=threading.RLock();self.active=0;self._lease=None
        self.check(self.generation)

    def check(self,generation):
        from spec_generator.stop_fence import StopRequested
        if generation!=self.generation:raise StopRequested('child generation changed')
        try:
            with self.saver._cursor():pass
        except TenantAccessDenied as exc:raise StopRequested('child execution stopped or authority lost') from exc

    @contextmanager
    def marker_guard(self):
        with self.saver._child_transaction():
            self.check(self.generation);yield

    def _writer_enter(self):
        with self.lock:
            if self.active==0:
                cap=self.documents.capability;key='pg-execution-writer:'+cap.scope.owner_id+':'+cap.session_id
                context=self.saver.store._borrow();conn=context.__enter__()
                try:
                    held=conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()['held']
                    conn.commit()
                    if not held:raise BlockingIOError('execution writer remains active')
                except BaseException:
                    import sys
                    context.__exit__(*sys.exc_info());raise
                self._lease=(context,conn,key)
            self.active+=1

    def _writer_exit(self):
        with self.lock:
            self.active-=1
            if self.active==0 and self._lease is not None:
                context,conn,key=self._lease;self._lease=None
                try:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,));conn.commit()
                finally:context.__exit__(None,None,None)

    @contextmanager
    def running(self):
        from spec_generator.stop_fence import execution_fence,_publish,_current
        self.check(self.generation);self._writer_enter()
        try:
            def publish(call):
                with self.marker_guard():return call()
            p=_publish.set(publish);c=_current.set(self)
            try:
                with execution_fence(lambda:self.check(self.generation)):yield self.generation
            finally:_current.reset(c);_publish.reset(p)
        finally:self._writer_exit()


class InstalledInterviewControlRouter:
    """Route preserved-source executions to DB control, never an interview graph."""
    def __init__(self,store,base):self.store=store;self.base=base

    def resolve(self,name,session_id):
        from .worker_capability import WorkerCapability
        from .tenancy import current_scope
        scope=current_scope()
        if not self.store.auth_session_active():raise TenantAccessDenied('installed router owner revoked')
        binding=self.store.session_binding(session_id)
        if not binding or binding['generation']==1:return None
        task=self.store.task(binding['task_id'])
        if not task or task['engine_session_id']!=session_id:raise TenantAccessDenied('installed control task changed')
        installed=self.store.installed_execution_anchor(session_id)
        if not installed:
            # Ordinary engine Stop bumps the lease generation as well. Only
            # that exact stopped first execution belongs to the normal registry;
            # missing lineage on a later execution must still fail closed.
            control=binding.get('control') or {}
            if isinstance(control,str):control=__import__('json').loads(control)
            if (binding['generation']==2 and control.get('stopped') is True
                and task['generation']==2 and task.get('stop_requested_at') is not None):
                return None
            raise TenantAccessDenied('installed control binding unavailable')
        cap=WorkerCapability(scope,task['task_id'],session_id,binding['generation'],installed['anchor'],installed['anchor_sha256'])
        from interview_engine.installed_execution import InstalledExecutionRegistry
        def read(owner,sid):
            if owner!=scope.owner_id or sid!=session_id:raise TenantAccessDenied('installed route changed')
            PostgresControlFence(self.store,cap)._validate()
            return self.store.installed_execution_anchor(sid)
        registry=InstalledExecutionRegistry(self.base,read_binding=read,fence_factory=lambda root,anchor:PostgresControlFence(self.store,cap))
        registry.get(session_id).validate()
        def snapshot(sid):
            from vmodel_contracts.interactive import EngineSnapshot,ProcessStatus,NextStep
            control=registry.get(sid)
            status=control.fence.state
            return EngineSnapshot(session_id=sid,task_id=cap.task_id,sequence=0,
                process_status=ProcessStatus.STOPPED if status=='STOPPED' else ProcessStatus.STOP_REQUESTED if status else ProcessStatus.COMPLETED,
                next_step=NextStep.STOP if status else NextStep.NONE)
        def mutation(sid,*args,**kwargs):
            from interview_engine.http_service import ProtocolFailure
            from vmodel_contracts.interactive import ProtocolErrorCode
            registry.get(sid).validate()
            raise ProtocolFailure(ProtocolErrorCode.INVALID_RESPONSE,'lineage execution preserves approved interview source; new interview input and route changes are unavailable')
        def stop(sid):registry.stop(sid);return snapshot(sid)
        def reconcile(sid):
            try:registry.reconcile_stopped(sid)
            except __import__('spec_generator.stop_fence',fromlist=['StopRequested']).StopRequested:pass
            return snapshot(sid)
        def progress(sid,*args,**kwargs):registry.get(sid).validate();raise KeyError(sid)
        return {'get':lambda sid,**kwargs:registry.get(sid),'snapshot':snapshot,'respond':mutation,'switch_route':mutation,
            'stop':stop,'reconcile_stopped':reconcile,'progress':progress}[name]
