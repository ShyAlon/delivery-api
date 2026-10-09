"""Captured worker authority. Never recover owner by client path/event scanning."""
from dataclasses import dataclass
from .tenancy import TenantScope,current_scope,TenantAccessDenied


@dataclass(frozen=True)
class WorkerCapability:
    scope: TenantScope
    task_id: str
    session_id: str
    generation: int

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
        return cls(scope,task_id,session,task['generation'])

    def validate(self,store):
        if current_scope()!=self.scope:
            raise TenantAccessDenied('worker scope changed')
        task=store.task(self.task_id)
        binding=store.session_binding(self.session_id)
        if not task or task['generation']!=self.generation or not binding or binding['task_id']!=self.task_id or binding['generation']!=self.generation:
            raise TenantAccessDenied('worker capability stale or revoked')
        if hasattr(store,'auth_session_active') and not store.auth_session_active():
            raise TenantAccessDenied('worker auth session revoked')
        return task
