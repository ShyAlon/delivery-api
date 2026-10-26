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
            except Exception as record_exc:
                from .observability import event as activity
                activity("job.failure_record_failed","A worker job failed AND its failure record could not be written; the job may look stuck",task_id=capability.task_id,job_id=job_id,error=type(record_exc).__name__)
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
    from .observability import event as activity
    requested={}
    if mode is not None:requested['mode']=mode.value if isinstance(mode,ExecutionMode) else mode
    if model_route is not None:requested['model_route']=model_route
    if resume_job_id and queued_job_id:raise ValueError("choose queued dispatch or explicit resume")
    if resume_job_id or queued_job_id:
        job=store.worker_job(resume_job_id or queued_job_id)
        if not job or job["task_id"]!=task_id or job["kind"]!="phase:"+phase:
            activity("phase.dispatch.rejected","Worker job dispatch rejected: job does not match task/phase",task_id=task_id,phase=phase,reason="job_mismatch")
            raise PermissionError("worker job does not match requested task/phase")
        durable=job["execution_options"]
        if any(durable.get(key)!=value for key,value in requested.items()):
            activity("phase.dispatch.rejected","Worker job dispatch rejected: options differ from reserved job",task_id=task_id,phase=phase,reason="options_mismatch")
            raise ValueError("execution options differ from reserved worker job")
    else:durable=requested
    if queued_job_id:
        if not job or job["task_id"]!=task_id or job["kind"]!="phase:"+phase or job["state"]!="pending":
            activity("phase.dispatch.rejected","Queued job dispatch rejected: no matching pending job",task_id=task_id,phase=phase,reason="queued_mismatch")
            raise PermissionError("queued job does not match requested task/phase")
        job_id=queued_job_id
    elif resume_job_id:
        job=store.worker_job(resume_job_id)
        if not job or job['task_id']!=task_id or job['kind']!='phase:'+phase:
            activity("phase.dispatch.rejected","Worker resume rejected: job does not match task/phase",task_id=task_id,phase=phase,reason="resume_mismatch")
            raise PermissionError('resume job does not match requested task/phase')
        store.reauthorize_worker(resume_job_id)
        job_id=resume_job_id
    else:job_id=store.enqueue_worker(task_id,'phase:'+phase,execution_options=durable)
    options={}
    if "mode" in durable:options["mode"]=ExecutionMode(durable["mode"])
    if "model_route" in durable:options["model_route"]=durable["model_route"]
    from . import config,inprocess_engine
    engine=inprocess_engine.current()
    if config.CLOUD_TENANTS and config.RUN_ISOLATION=="process":
        # One child process per run, admitted through its own descriptor. Slots, time limit and peak memory live in run_slots.
        from . import run_slots
        async def run_child(capability):
            capability.validate(store)
            from . import child_compute,cloud_runtime,producer_admission
            from .attempt_ledger import AdmissionDenied
            factory=cloud_runtime.gateway_factory()
            if factory is None:raise AdmissionDenied('cloud execution is not installed on this server')
            # The child self-wires its admission from the descriptor the runner hands it;
            # this process supplies the job-bound gateway for the lifetime of the run.
            with producer_admission.worker_gateway(factory(capability,job_id)):
                segment=child_compute.begin(store,job_id,phase) if child_compute.available(store) else None
                try:
                    await orchestrator.execute_phase(capability.task_id,phase,**options)
                finally:
                    # Charged for the CPU the child used, also when it failed, was stopped or hit the time limit.
                    if segment is not None:
                        cpu=run_slots.running.get(task_id,{}).get("cpu_seconds")
                        try:child_compute.settle(store,segment,cpu)
                        except Exception as exc:
                            from .observability import event as activity
                            activity("run.compute_unsettled","The run's compute charge could not be posted; it is marked unresolved for review",task_id=task_id,error=type(exc).__name__,detail=str(exc)[:120])
                            try:store.finish_retail_compute(segment,None)
                            except Exception:pass
        started=await run_slots.run_limited(store,orchestrator,task_id,lambda:execute_job(store,job_id,run_child))
        if not started:
            activity("phase.dispatch.rejected","Worker job was already claimed; no execution started",task_id=task_id,phase=phase,job_id=job_id,reason="already_claimed")
            raise ValueError("worker job was already claimed; no execution started")
        activity("phase.dispatch.claimed","Worker job claimed and started",task_id=task_id,phase=phase,job_id=job_id)
        return job_id
    if config.CLOUD_TENANTS and engine is not None:
        # The service runs the generator itself under the money gateway; it claims the job and meters it.
        if not await engine.dispatch(store,orchestrator,task_id,phase,job_id,options):
            activity("phase.dispatch.rejected","Worker job was already claimed; no execution started",task_id=task_id,phase=phase,job_id=job_id,reason="already_claimed")
            raise ValueError("worker job was already claimed; no execution started")
        activity("phase.dispatch.claimed","Worker job claimed and started",task_id=task_id,phase=phase,job_id=job_id)
        return job_id
    async def run(capability):
        capability.validate(store)
        await orchestrator.execute_phase(capability.task_id,phase,**options)
    if not await execute_job(store,job_id,run):
        activity("phase.dispatch.rejected","Worker job was already claimed; no execution started",task_id=task_id,phase=phase,job_id=job_id,reason="already_claimed")
        raise ValueError("worker job was already claimed; no execution started")
    activity("phase.dispatch.claimed","Worker job claimed and started",task_id=task_id,phase=phase,job_id=job_id)
    return job_id
