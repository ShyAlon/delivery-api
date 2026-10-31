"""Private reusable in-process producer worker. No queue scan or public route.

An explicit owner-authorized durable job supplies all authority. Accepted calls
finish on their threads; the engine and producer use the same filesystem marker.
This seam neither enables cloud execution nor replaces the SDK wire adapter.
"""
class ProducerRecoveryPaused(Exception):
    """Paid work may already exist but its durable publication state is missing or
    unverifiable. The job PAUSES with the specific evidence; paid work is never
    regenerated blind."""


def _produced_set_hash(root):
    from .artifact_publication import artifact_set_hash,file_artifact_snapshot
    return artifact_set_hash(file_artifact_snapshot(root))


import asyncio,inspect
from contextlib import ExitStack
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied,current_scope
from .tenant_jobs import execute_job


class SameHostProducerWorker:
    def __init__(self,store,artifacts_base,*,gateway_factory,usage_log_factory,wire_client_factory=None,runtime_factory=None,isolated_quiescent=None,documents_enabled=False,isolated_accounting=None):
        self.store=store
        self.artifacts_base=artifacts_base
        self.gateway_factory=gateway_factory
        self.usage_log_factory=usage_log_factory
        self.wire_client_factory=wire_client_factory
        self.runtime_factory=runtime_factory
        self.documents_enabled=documents_enabled
        self.isolated_quiescent=isolated_quiescent
        if isolated_accounting is not None:
            from .child_paid_runtime import PaidChildRuntime
            if not isinstance(isolated_accounting,PaidChildRuntime) or isolated_quiescent is None:
                raise TypeError('typed isolated accounting and quiescence required')
        self.isolated_accounting=isolated_accounting

    async def execute(self,job_id,producer):
        """Claim once, bind a synchronous producer, never restart automatically.

        The trusted callback owns validated CLI args or graph construction. It
        runs off the event loop, with captured owner scope and admission context.
        No producer input chooses a marker, owner, generation, or gateway.
        """
        from .execution_fence import require_engine_execution
        require_engine_execution()
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
            if hasattr(self.store,'_postgres'):
                from .pg_execution_fence import PostgresExecutionFence
                fence=PostgresExecutionFence(self.store,capability,root=root)
            else:
                fence=StopFence(root/'interview'/'stop-request.json',execution_anchor=capability.execution_anchor)
                if capability.execution_anchor is not None:
                    from vmodel_contracts.execution_anchor import read_execution_anchor
                    if read_execution_anchor(root,expected=capability.execution_anchor)['document_sha256']!=capability.anchor_sha256:
                        raise TenantAccessDenied('installed anchor bytes differ from database')
            if fence.generation!=capability.generation or fence.requested:
                raise TenantAccessDenied('producer stop generation mismatch')
            gateway=self.gateway_factory(capability,job_id)
            if self.isolated_quiescent is not None and getattr(gateway,'retail_rates',None) is not None and self.isolated_accounting is None:
                raise PermissionError('paid child producer remains refused')
            # A caller cannot accidentally lend another job's gateway.
            if gateway.store is not self.store or gateway.job_id!=job_id:
                raise TenantAccessDenied('producer gateway binding mismatch')
            usage_log=self.usage_log_factory(capability,root)
            from . import config as _config
            documents=None
            if hasattr(self.store,'_postgres') and (self.runtime_factory is not None or self.documents_enabled):
                from .generator_documents import GeneratorDocuments,prepare_document_workspace,document_set_hash,pin_document_artifacts,document_substitutions
                attempt=self.store.worker_job(job_id)['attempt']
                documents=GeneratorDocuments(self.store,capability,job_id,expected_attempt=attempt)
            # A closed-gate proposal is private preparation, not a live phase
            # result. Its retained proposal is reviewed before any installation.
            phase_job=self.store.worker_job(job_id)['kind'].startswith('phase:')
            publishing=phase_job and (documents is not None or _config.ARTIFACT_PUBLISH) and hasattr(self.store,'publish_task_revision')
            pending=self.store.producer_result(job_id) if publishing else None
            # Recovery case: the paid producer ALREADY completed for this generation and
            # only publication failed. Never re-run the paid callable or rebill - reuse
            # the exact durable bytes and retry publication alone.
            resuming_publication=pending is not None and pending['generation']==capability.generation
            if publishing and pending is not None and not resuming_publication:
                raise ProducerRecoveryPaused(f"produced marker names generation {pending['generation']}, job runs generation {capability.generation}; reconcile before any rerun")
            if publishing and pending is None and self.store.producer_paid_evidence(job_id):
                raise ProducerRecoveryPaused(f'no produced marker for job {job_id} but provider attempt row(s) exist; the produce may have completed and crashed before the marker - reconcile, never blind-rerun paid work')
            if resuming_publication:
                current,files=document_set_hash(documents) if documents is not None else _produced_set_hash(root)
                if files==0:
                    raise ProducerRecoveryPaused(f'produced bytes for job {job_id} are not on this host/path ({root}) and no published manifest exists; restore staged bytes or reconcile - never regenerate paid work')
                if current!=pending['content_hash']:
                    raise ProducerRecoveryPaused(f'produced bytes for job {job_id} differ from the recorded content hash; reconcile - never publish or regenerate from an unverified path')
            if documents is not None and not resuming_publication:
                prepare_document_workspace(self.store,capability.task_id,documents,current_phase=self.store.worker_job(job_id)['kind'].split(':')[-1])
            settled_pin=None
            settled_phase=None
            if publishing and not resuming_publication:
                from .phases import PHASE_BY_KEY as _PHASE_BY_KEY
                _job=self.store.worker_job(job_id) or {}
                _kind=_job.get('kind','')
                _phase_def=_PHASE_BY_KEY.get(_kind.split(':')[-1])
                if _phase_def is not None and _phase_def.executor=='generator':
                    from .artifact_publication import pin_settled_artifacts
                    _pin_task=self.store.task(capability.task_id)
                    if _pin_task:
                        settled_pin=pin_document_artifacts(_pin_task,documents,current_phase=_kind.split(':')[-1]) if documents is not None else pin_settled_artifacts(_pin_task,root,current_phase=_kind.split(':')[-1])
                        settled_phase=_phase_def.key
            def invoke():
                capability.validate(self.store)
                with ExitStack() as stack:
                    if self.runtime_factory is not None:
                        from spec_generator.worker_runtime import job_runtime
                        runtime=self.runtime_factory(capability,root)
                        runtime.bind_worker(owner_id=capability.scope.owner_id,task_id=capability.task_id,
                            job_id=job_id,generation=capability.generation,session_root=root)
                        if hasattr(self.store,'_postgres') and self.isolated_quiescent is None:
                            from .generator_checkpoint import GeneratorCheckpoint
                            attempt=self.store.worker_job(job_id)['attempt']
                            runtime.checkpoint_factory=lambda namespace:GeneratorCheckpoint(self.store,capability,job_id,namespace,expected_attempt=attempt)
                            if documents is not None:runtime.proposal_sink=documents.retain_proposal
                        runtime.documents=documents
                        stack.enter_context(job_runtime(runtime))
                    stack.enter_context(admission_gateway(gateway,required=True))
                    if self.isolated_quiescent is None:
                        stack.enter_context(cli_execution(fence,usage_log))
                    if self.wire_client_factory is not None:
                        from spec_generator.wire_binding import wire_clients
                        stack.enter_context(wire_clients(gateway,self.wire_client_factory))
                    if getattr(gateway,'retail_rates',None) is not None and self.isolated_accounting is None:
                        from .compute_meter import ComputeMeter,bind
                        segment=job_id+':compute'
                        job=self.store.worker_job(job_id)
                        self.store.begin_retail_compute(segment,job_id,job['kind'].split(':')[-1],gateway.retail_rates.snapshot())
                        meter=ComputeMeter();gateway.compute_meter=meter
                        try:
                            with bind(meter):result=None if resuming_publication else producer(capability,root)
                            if inspect.isawaitable(result):
                                if inspect.iscoroutine(result):result.close()
                                raise TypeError('synchronous producer required')
                            self.store.finish_retail_compute(segment,meter.milliseconds())
                        except BaseException:
                            self.store.close_failed_retail_compute(segment)
                            raise
                    else:
                        result=None if resuming_publication else producer(capability,root)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):result.close()
                        raise TypeError('synchronous producer required')
                    if publishing:
                        if settled_pin is not None:
                            from .artifact_publication import settled_substitutions, format_settled_substitution
                            problems=document_substitutions(documents,*settled_pin) if documents is not None else settled_substitutions(root,*settled_pin)
                            if problems:
                                error=format_settled_substitution(settled_phase,problems)
                                self.store.update_phase(capability.task_id,settled_phase,"failed",detail=error)
                                self.store.add_event(capability.task_id,"error",error,phase_key=settled_phase,payload={"substitutions":[list(p) for p in problems]})
                                from .observability import event as _activity
                                _activity("settled_substitution.blocked","Settled-artifact substitution blocked before publication",task_id=capability.task_id,phase=settled_phase,substitutions=str([list(p) for p in problems]))
                                raise ValueError(error)
                        if not resuming_publication:
                            content_hash,_=document_set_hash(documents) if documents is not None else _produced_set_hash(root)
                            self.store.record_producer_result(job_id,capability.task_id,capability.generation,str(root),content_hash)
                        # Publication failure fails the job loudly; the durable
                        # producer_results row makes the resume publication-only.
                        from .artifact_publication import publish_session_revision
                        if documents is not None:
                            from .artifact_publication import publish_document_revision
                            publish_document_revision(self.store,capability.task_id,documents)
                        else:publish_session_revision(self.store,capability.task_id,root)
                        self.store.mark_producer_published(job_id)
            try:await asyncio.to_thread(invoke)
            except StopRequested:
                task=self.store.task(capability.task_id)
                if not task or task.get('stop_requested_at') is None:raise
                # Expected Stop is control state, not a failed producer job.
        def quiescent(capability):
            if self.isolated_quiescent is not None and self.isolated_quiescent() is not True:return False
            if fence is None or not fence.state:return False
            try:return fence.confirm_quiescent_after_recovery(no_live_worker=True)=='STOPPED'
            except StopRequested:return False
        return await execute_job(self.store,job_id,run,verify_stopped_quiescence=quiescent,
            verify_completion_quiescence=lambda cap:fence is not None and fence.writer_quiescent() and (self.isolated_quiescent is None or self.isolated_quiescent() is True))

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
        if hasattr(self.store,'_postgres'):
            if self.isolated_quiescent is not None and self.isolated_quiescent() is not True:return False
            if not self.store.auth_session_active():raise TenantAccessDenied('stopped worker owner revoked')
            if job['generation']>1:
                installed=self.store.installed_execution_anchor(job['session_id'])
                if not installed or installed['anchor'].task_id!=job['task_id'] or installed['anchor'].generation!=job['generation']:
                    raise TenantAccessDenied('stopped installed lineage differs')
            key='pg-execution-writer:'+scope.owner_id+':'+job['session_id']
            with self.store._postgres.transaction() as conn:
                held=conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()['held']
                if not held:return False
                try:
                    self.store.finish_stopped_worker(job_id,worker_quiescent=True)
                finally:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
            return True
        root=session_root(self.artifacts_base,scope,job['session_id'])
        marker=root/'interview'/'stop-request.json'
        # Resolve the exact marker and lock siblings before accessing them.
        for path in (marker,marker.with_suffix('.marker-lock'),marker.with_suffix('.writer-lock')):
            if path.resolve()!=path:raise TenantAccessDenied('unsafe stop control path')
        anchor=None
        if job['generation']>1:
            installed=self.store.installed_execution_anchor(job['session_id'])
            if not installed:raise TenantAccessDenied('original installed stopped binding required')
            anchor=installed['anchor']
            if anchor.task_id!=job['task_id'] or anchor.generation!=job['generation']:
                raise TenantAccessDenied('stopped installed lineage differs from original job')
            from vmodel_contracts.execution_anchor import read_execution_anchor
            if read_execution_anchor(root,expected=anchor)['document_sha256']!=installed['anchor_sha256']:
                raise TenantAccessDenied('stopped installed anchor bytes changed')
        fence=StopFence(marker,execution_anchor=anchor)
        if not fence.requested or fence.generation!=task['generation']:
            raise TenantAccessDenied('durable stopped generation required')
        try:fence.confirm_quiescent_after_recovery(no_live_worker=True)
        except StopRequested:return False
        self.store.finish_stopped_worker(job_id,worker_quiescent=True)
        return True
