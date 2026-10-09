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

    def validate(self,store):
        if current_scope()!=self.scope:
            raise TenantAccessDenied('worker scope changed')
        task=store.task(self.task_id)
        binding=store.session_binding(self.session_id)
        if not task or task['generation']!=self.generation or not binding or binding['task_id']!=self.task_id or binding['generation']!=self.generation:
            raise TenantAccessDenied('worker capability stale or revoked')
        if self.generation>1:
            if self.execution_anchor is None or not hasattr(store,'installed_execution_anchor'):
                raise TenantAccessDenied('installed worker lineage missing')
            installed=store.installed_execution_anchor(self.session_id)
            if not installed or installed!={'anchor':self.execution_anchor,'anchor_sha256':self.anchor_sha256}:
                raise TenantAccessDenied('installed worker lineage changed')
        if hasattr(store,'auth_session_active') and not store.auth_session_active():
            raise TenantAccessDenied('worker auth session revoked')
        return task
