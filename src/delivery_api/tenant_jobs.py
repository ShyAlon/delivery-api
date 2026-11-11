"""Bounded job dispatch with durable owner capability and explicit resume.

No automatic unscoped global recovery. The owner supplies a currently verified
Auth session to re-authorize interrupted work; rate/spend admission is separate.
"""
from .tenancy import current_scope,tenant_context


async def execute_job(store,job_id,handler,*,verify_stopped_quiescence=None,verify_completion_quiescence=None,reuse_answer_authority=False,claimed_answer=None):
    scope=current_scope()
    with tenant_context(scope):
        if claimed_answer is not None:
            if not reuse_answer_authority or claimed_answer[0].scope!=scope or claimed_answer[2]!=job_id:raise PermissionError('scoped answer entry required')
            capability=claimed_answer[0]
        else:capability=store.claim_worker(job_id)
        if capability is None:return False
        from .diagnostic_evidence import bound
        with bound(task_id=capability.task_id,session_id=capability.session_id,job_id=job_id):
            try:
                task=claimed_answer[1] if claimed_answer is not None else capability.validate(store)
                from contextlib import nullcontext
                from .worker_capability import answer_authority
                with answer_authority(store,capability,task) if reuse_answer_authority else nullcontext():
                    completion=await handler(capability)
                if reuse_answer_authority:
                    def finish_answer():
                        # Fresh final authority in one short transaction. The
                        # immutable capability is reused only for nested helpers.
                        fresh=capability.validate(store)
                        if fresh.get('stop_requested_at') is not None:
                            if verify_stopped_quiescence is not None and verify_stopped_quiescence(capability) is True:
                                store.finish_stopped_worker(job_id,worker_quiescent=True)
                            return True
                        if verify_completion_quiescence is not None and verify_completion_quiescence(capability) is not True:return True
                        if completion is None:raise PermissionError('answer completion evidence missing')
                        record,event=completion
                        store.retain_answer_review(capability.task_id,'child-job:'+job_id,'child-completion-v1',record)
                        store.add_event(capability.task_id,'child.completed',event[0],payload=event[1])
                        store.finish_answer_worker(capability,job_id)
                        return True
                    return store.run_answer_batch(finish_answer)
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
    from . import config,inprocess_engine
    if (inprocess_engine.current() is None):
        from .attempt_ledger import AdmissionDenied
        raise AdmissionDenied('child execution is not installed on this server')
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
    if engine is not None:
        # The service runs the generator itself under the money gateway; it claims the job and meters it.
        if not await engine.dispatch(store,orchestrator,task_id,phase,job_id,options):
            activity("phase.dispatch.rejected","Worker job was already claimed; no execution started",task_id=task_id,phase=phase,job_id=job_id,reason="already_claimed")
            raise ValueError("worker job was already claimed; no execution started")
        activity("phase.dispatch.claimed","Worker job claimed and started",task_id=task_id,phase=phase,job_id=job_id)
        return job_id
