"""Live parent-side operation authority, separate from the child's wire data."""
from .tenancy import current_scope,TenantAccessDenied
from .worker_capability import WorkerCapability


class ChildBrokerAuthority:
    def __init__(self,store,operation,*,lease_epoch):
        # Engine compare-and-claim increments the pre-claim epoch. Only trusted
        # wiring may supply the child's won epoch; never accept it from a call.
        self.store=store;self.operation=operation;self.lease_epoch=lease_epoch

    def __call__(self,operation,call):
        if operation!=self.operation:raise TenantAccessDenied('broker operation changed')
        b=operation.binding;scope=current_scope()
        if (scope.owner_id,scope.session_id)!=(str(b.owner_id),str(b.auth_session_id)):
            raise TenantAccessDenied('broker original owner/Auth-session required')
        cap=WorkerCapability.capture(self.store,b.task_id);task=cap.validate(self.store)
        job=self.store.worker_job(b.job_id);session=self.store.session_binding(b.session_id)
        if (not self.store.auth_session_active() or not job or not session or job['state']!='running'
            or str(job['owner_id'])!=scope.owner_id or str(job['auth_session_id'])!=scope.session_id
            or job['task_id']!=b.task_id or job['session_id']!=b.session_id or job['generation']!=b.generation
            or cap.session_id!=b.session_id or cap.generation!=b.generation
            or task.get('stop_requested_at') is not None or session.get('lease_epoch')!=self.lease_epoch
            or session.get('lease_expires_at',0)<=__import__('time').time()):
            raise TenantAccessDenied('broker live job/lease/generation stopped or changed')
        selected=(job.get('execution_options') or {}).get('model_route')
        if selected!=b.provider+':'+b.model or (call.provider,call.model)!=(b.provider,b.model):
            raise TenantAccessDenied('broker durable provider/model selection changed')
        if b.generation>1 and cap.anchor_sha256!=b.execution_anchor_sha256:
            raise TenantAccessDenied('broker installed execution anchor changed')
