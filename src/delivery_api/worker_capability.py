"""Captured worker authority. Never recover owner by client path/event scanning."""
from dataclasses import dataclass
from .tenancy import TenantScope,current_scope,TenantAccessDenied


@dataclass(frozen=True)
class WorkerCapability:
    scope: TenantScope
    task_id: str
    session_id: str
    generation: int
    execution_anchor: object = None
    anchor_sha256: str | None = None

    @classmethod
    def capture(cls,store,task_id):
        scope=current_scope()
        task=store.task(task_id)
        if not task or task['owner_id']!=scope.owner_id:
            raise TenantAccessDenied('authorized worker task required')
        session=store.engine_session(task_id)
        binding=store.session_binding(session) if session else None
        if not binding or binding['task_id']!=task_id or str(binding['owner_id'])!=scope.owner_id or binding['generation']!=task['generation']:
            raise TenantAccessDenied('current generation session binding required')
        anchor=None;anchor_sha256=None
        if task['generation']>1:
            if not hasattr(store,'installed_execution_anchor'):raise TenantAccessDenied('installed lineage reader required')
            installed=store.installed_execution_anchor(session)
            if not installed:raise TenantAccessDenied('later-generation installed lineage required')
            anchor=installed['anchor'];anchor_sha256=installed['anchor_sha256']
            if (anchor.owner_id!=scope.owner_id or anchor.task_id!=task_id or anchor.execution_session_id!=session
                or anchor.generation!=task['generation']):raise TenantAccessDenied('installed worker lineage mismatch')
        return cls(scope,task_id,session,task['generation'],anchor,anchor_sha256)

    def validate_job(self,job,*,state=None,attempt=None):
        if (not job or job['task_id']!=self.task_id or job['generation']!=self.generation
            or job['session_id']!=self.session_id or str(job['owner_id'])!=self.scope.owner_id
            or str(job['auth_session_id'])!=self.scope.session_id or (state is not None and job['state']!=state)):
            raise TenantAccessDenied('worker job authority changed')
        if attempt is not None and job['attempt']!=attempt:raise TenantAccessDenied('generator attempt changed')

    @classmethod
    def require_job(cls,store,job):
        """Fresh shared owner/job/session/generation authority, never cached admission."""
        if not job:raise TenantAccessDenied('worker job required')
        if hasattr(store,'worker_authority'):cap,task=cls._capture_and_validate(store,job['task_id'])
        else:cap=cls.capture(store,job['task_id']);task=cap.validate(store)
        if task.get('stop_requested_at') is not None:raise TenantAccessDenied('worker stopped')
        cap.validate_job(job)
        return cap

    @classmethod
    def _capture_and_validate(cls,store,task_id):
        """capture + validate from one read. Same checks and messages; the second read of the same rows is gone."""
        scope=current_scope()
        task,session,binding,active=store.worker_authority(task_id)
        if not task or task['owner_id']!=scope.owner_id:
            raise TenantAccessDenied('authorized worker task required')
        if not session or not binding or binding['task_id']!=task_id or str(binding['owner_id'])!=scope.owner_id or binding['generation']!=task['generation']:
            raise TenantAccessDenied('current generation session binding required')
        anchor=None;anchor_sha256=None
        if task['generation']>1:
            if not hasattr(store,'installed_execution_anchor'):raise TenantAccessDenied('installed lineage reader required')
            installed=store.installed_execution_anchor(session)
            if not installed:raise TenantAccessDenied('later-generation installed lineage required')
            anchor=installed['anchor'];anchor_sha256=installed['anchor_sha256']
            if (anchor.owner_id!=scope.owner_id or anchor.task_id!=task_id or anchor.execution_session_id!=session
                or anchor.generation!=task['generation']):raise TenantAccessDenied('installed worker lineage mismatch')
        if not active:raise TenantAccessDenied('worker auth session revoked')
        return cls(scope,task_id,session,task['generation'],anchor,anchor_sha256),task

    def validate(self,store):
        if current_scope()!=self.scope:
            raise TenantAccessDenied('worker scope changed')
        merged=hasattr(store,'authority_snapshot')
        if merged:task,binding,active=store.authority_snapshot(self.task_id,self.session_id)
        else:task=store.task(self.task_id);binding=store.session_binding(self.session_id);active=None
        if not task or task['generation']!=self.generation or not binding or binding['task_id']!=self.task_id or binding['generation']!=self.generation:
            raise TenantAccessDenied('worker capability stale or revoked')
        if self.generation>1:
            if self.execution_anchor is None or not hasattr(store,'installed_execution_anchor'):
                raise TenantAccessDenied('installed worker lineage missing')
            installed=store.installed_execution_anchor(self.session_id)
            if not installed or installed!={'anchor':self.execution_anchor,'anchor_sha256':self.anchor_sha256}:
                raise TenantAccessDenied('installed worker lineage changed')
        if (not active if merged else hasattr(store,'auth_session_active') and not store.auth_session_active()):
            raise TenantAccessDenied('worker auth session revoked')
        return task
