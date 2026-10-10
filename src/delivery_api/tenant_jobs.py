"""Bounded job dispatch with durable owner capability and explicit resume.

No automatic unscoped global recovery. The owner supplies a currently verified
Auth session to re-authorize interrupted work; rate/spend admission is separate.
"""
from .tenancy import current_scope,tenant_context


async def execute_job(store,job_id,handler,*,verify_stopped_quiescence=None,verify_completion_quiescence=None):
    from .execution_fence import require_engine_execution
    require_engine_execution()
    scope=current_scope()
    with tenant_context(scope):
        capability=store.claim_worker(job_id)
        if capability is None:return False
        try:
            capability.validate(store)
            await handler(capability)
            task=store.task(capability.task_id)
            if task and task.get('stop_requested_at') is not None:
                # Handler return, cancellation, timeout or an engine snapshot
                # alone is not proof that its provider threads exited.
                if verify_stopped_quiescence is not None and verify_stopped_quiescence(capability) is True:
                    store.finish_stopped_worker(job_id,worker_quiescent=True)
                return True
            capability.validate(store)
        except Exception:
            # Preserve original failure even if sign-out removed DB authority.
            # The row remains running and requires owner reauthorization/review.
            try:
                task=store.task(capability.task_id)
                if task and task.get('stop_requested_at') is not None:
                    if verify_stopped_quiescence is not None and verify_stopped_quiescence(capability) is True:
                        store.finish_stopped_worker(job_id,worker_quiescent=True)
                elif verify_completion_quiescence is None or verify_completion_quiescence(capability) is True:
                    store.finish_worker(job_id,failed=True)
            except Exception:pass
            raise
        # A returned handler can leave deadline/provider writer threads alive.
        # The private persistent runner supplies its same-host lock verifier.
        if verify_completion_quiescence is not None and verify_completion_quiescence(capability) is not True:
            return True
        store.finish_worker(job_id)
        return True


async def dispatch_phase(store,orchestrator,task_id,phase,*,resume_job_id=None,queued_job_id=None,mode=None,model_route=None):
    """Reviewed cloud phase seam. Caller owns rate/spend admission before dispatch.

    Not used by public routes until that admission boundary and full lifecycle
    integration are complete. Resume is an explicit current-owner operation.
    """
    from .orchestrator import ExecutionMode
    requested={}
    if mode is not None:requested['mode']=mode.value if isinstance(mode,ExecutionMode) else mode
    if model_route is not None:requested['model_route']=model_route
    if resume_job_id and queued_job_id:raise ValueError("choose queued dispatch or explicit resume")
    if resume_job_id or queued_job_id:
        job=store.worker_job(resume_job_id or queued_job_id)
        if not job or job["task_id"]!=task_id or job["kind"]!="phase:"+phase:
            raise PermissionError("worker job does not match requested task/phase")
        durable=job["execution_options"]
        if any(durable.get(key)!=value for key,value in requested.items()):
            raise ValueError("execution options differ from reserved worker job")
    else:durable=requested
    if queued_job_id:
        if not job or job["task_id"]!=task_id or job["kind"]!="phase:"+phase or job["state"]!="pending":
            raise PermissionError("queued job does not match requested task/phase")
        job_id=queued_job_id
    elif resume_job_id:
        job=store.worker_job(resume_job_id)
        if not job or job['task_id']!=task_id or job['kind']!='phase:'+phase:
            raise PermissionError('resume job does not match requested task/phase')
        store.reauthorize_worker(resume_job_id)
        job_id=resume_job_id
    else:job_id=store.enqueue_worker(task_id,'phase:'+phase,execution_options=durable)
    async def run(capability):
        capability.validate(store)
        options={}
        if "mode" in durable:options["mode"]=ExecutionMode(durable["mode"])
        if "model_route" in durable:options["model_route"]=durable["model_route"]
        await orchestrator.execute_phase(capability.task_id,phase,**options)
    if not await execute_job(store,job_id,run):
        raise ValueError("worker job was already claimed; no execution started")
    return job_id
