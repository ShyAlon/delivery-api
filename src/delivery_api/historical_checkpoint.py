"""Historical checkpoint read authority is never a worker or write capability."""
from dataclasses import dataclass
from .tenancy import current_scope,TenantAccessDenied

@dataclass(frozen=True)
class HistoricalCheckpointCapability:
    scope:object
    task_id:str
    session_id:str
    generation:int

    def validate(self,store):
        if current_scope()!=self.scope or not store.auth_session_active():raise TenantAccessDenied('historical checkpoint owner revoked')
        task=store.task(self.task_id);binding=store.session_binding(self.session_id)
        if (not task or task['owner_id']!=self.scope.owner_id or task['generation']<self.generation
            or not binding or str(binding['owner_id'])!=self.scope.owner_id or binding['task_id']!=self.task_id
            or binding['generation']!=self.generation):raise TenantAccessDenied('historical checkpoint binding differs')
        return task
