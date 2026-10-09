"""Private reusable in-process producer worker. No queue scan or public route.

An explicit owner-authorized durable job supplies all authority. Accepted calls
finish on their threads; the engine and producer use the same filesystem marker.
This seam neither enables cloud execution nor replaces the SDK wire adapter.
"""
import asyncio,inspect
from contextlib import ExitStack
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied,current_scope
from .tenant_jobs import execute_job


class SameHostProducerWorker:
    def __init__(self,store,artifacts_base,*,gateway_factory,usage_log_factory,wire_client_factory=None,runtime_factory=None):
        self.store=store
        self.artifacts_base=artifacts_base
        self.gateway_factory=gateway_factory
        self.usage_log_factory=usage_log_factory
        self.wire_client_factory=wire_client_factory
        self.runtime_factory=runtime_factory

    async def execute(self,job_id,producer):
        """Claim once, bind a synchronous producer, never restart automatically.

        The trusted callback owns validated CLI args or graph construction. It
        runs off the event loop, with captured owner scope and admission context.
        No producer input chooses a marker, owner, generation, or gateway.
        """
        if inspect.iscoroutinefunction(producer):raise TypeError('synchronous producer required')
        from spec_generator.stop_fence import StopFence,StopRequested
        from spec_generator.cooperative_run import cli_execution
        from spec_generator.attempt_admission import admission_gateway
        fence=None
        async def run(capability):
            nonlocal fence
            capability.validate(self.store)
            root=session_root(self.artifacts_base,capability.scope,capability.session_id)
            # Match interview-engine's existing canonical control marker.
            # Reject unsafe existing internal paths before constructing a fence.
            import os
            if root.exists():
                for directory,dirs,files in os.walk(root,followlinks=False):
                    if any((Path(directory)/name).is_symlink() for name in dirs+files):
                        raise TenantAccessDenied('session contains an unsafe symlink')
            fence=StopFence(root/'interview'/'stop-request.json',execution_anchor=capability.execution_anchor)
            if capability.execution_anchor is not None:
                from vmodel_contracts.execution_anchor import read_execution_anchor
                if read_execution_anchor(root,expected=capability.execution_anchor)['document_sha256']!=capability.anchor_sha256:
                    raise TenantAccessDenied('installed anchor bytes differ from database')
            if fence.generation!=capability.generation or fence.requested:
                raise TenantAccessDenied('producer stop generation mismatch')
            gateway=self.gateway_factory(capability,job_id)
            # A caller cannot accidentally lend another job's gateway.
            if gateway.store is not self.store or gateway.job_id!=job_id:
                raise TenantAccessDenied('producer gateway binding mismatch')
            usage_log=self.usage_log_factory(capability,root)
            def invoke():
                capability.validate(self.store)
                with ExitStack() as stack:
                    if self.runtime_factory is not None:
                        from spec_generator.worker_runtime import job_runtime
                        runtime=self.runtime_factory(capability,root)
                        runtime.bind_worker(owner_id=capability.scope.owner_id,task_id=capability.task_id,
                            job_id=job_id,generation=capability.generation,session_root=root)
                        stack.enter_context(job_runtime(runtime))
                    stack.enter_context(admission_gateway(gateway,required=True))
                    stack.enter_context(cli_execution(fence,usage_log))
                    if self.wire_client_factory is not None:
                        from spec_generator.wire_binding import wire_clients
                        stack.enter_context(wire_clients(gateway,self.wire_client_factory))
                    result=producer(capability,root)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):result.close()
                        raise TypeError('synchronous producer required')
            try:await asyncio.to_thread(invoke)
            except StopRequested:
                task=self.store.task(capability.task_id)
                if not task or task.get('stop_requested_at') is None:raise
                # Expected Stop is control state, not a failed producer job.
        def quiescent(capability):
            if fence is None or not fence.state:return False
            try:return fence.confirm_quiescent_after_recovery(no_live_worker=True)=='STOPPED'
            except StopRequested:return False
        return await execute_job(self.store,job_id,run,verify_stopped_quiescence=quiescent,
            verify_completion_quiescence=lambda cap:fence is not None and fence.writer_quiescent())

    def reconcile_stopped(self,job_id):
        """Explicit owner recovery, not a timer or automatic resume.

        Closing the writer job never releases provider holds or marks the task
        STOPPED. The task coordinator still checks engine and ledger evidence.
        """
        from spec_generator.stop_fence import StopFence,StopRequested
        job=self.store.worker_job(job_id)
        if not job:raise KeyError(job_id)
        scope=current_scope();task=self.store.task(job['task_id'])
        binding=self.store.session_binding(job['session_id'])
        if (not task or task.get('stop_requested_at') is None
            or str(job['owner_id'])!=scope.owner_id
            or str(job['auth_session_id'])!=scope.session_id
            or task['generation']!=job['generation']+1
            or not binding or binding['task_id']!=job['task_id']
            or str(binding['owner_id'])!=scope.owner_id):
            raise TenantAccessDenied('original stopped worker binding required')
        if job['state']=='done':return True
        if job['state']!='running':raise TenantAccessDenied('running stopped worker required')
        root=session_root(self.artifacts_base,scope,job['session_id'])
        marker=root/'interview'/'stop-request.json'
        # Resolve the exact marker and lock siblings before accessing them.
        for path in (marker,marker.with_suffix('.marker-lock'),marker.with_suffix('.writer-lock')):
            if path.resolve()!=path:raise TenantAccessDenied('unsafe stop control path')
        fence=StopFence(marker)
        if not fence.requested or fence.generation!=task['generation']:
            raise TenantAccessDenied('durable stopped generation required')
        try:fence.confirm_quiescent_after_recovery(no_live_worker=True)
        except StopRequested:return False
        self.store.finish_stopped_worker(job_id,worker_quiescent=True)
        return True
