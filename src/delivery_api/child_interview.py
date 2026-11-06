"""Interview child client with claimless parent reads and fresh children.

Paid routes require an internal accounting seam; production activation stays off. A failure pauses the durable job, never falls back
to an in-process graph. One process-wide child slot includes reap/reconciliation.
"""
import asyncio,json,sys,threading,time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from .engine_client import EngineClient
from .child_supervisor import ChildSupervisor

_slot=threading.Lock()

class LocalComputeEvidence:
    def __init__(self,persist=None):
        self.persist=persist;self.transcript=[]
        from .child_compute_events import ChildComputeEvents
        self.events=ChildComputeEvents();self.settled=False;self.value=None
    def begin(self):pass
    def accept(self,event):
        if len(self.transcript)>=10000:raise ValueError('bounded local compute transcript required')
        self.events.accept(event);self.transcript.append(event)
    def finish(self):
        self.value=self.events.milliseconds()
        if self.persist is not None:self.persist({'events':self.transcript,'milliseconds':str(self.value)})
        self.settled=True
    def abort(self):pass


class ChildInterviewClient(EngineClient):
    def __init__(self,store,artifacts_base,*,gateway_factory,dsn,endpoint,command=None,paid_runtime=None):
        if paid_runtime is not None:
            from .child_paid_runtime import PaidChildRuntime
            if not isinstance(paid_runtime,PaidChildRuntime):raise TypeError('typed parent accounting runtime required')
        self.paid_runtime=paid_runtime
        self.store=store;self.base=Path(artifacts_base);self.gateway_factory=gateway_factory;self.dsn=dsn;self.endpoint=endpoint
        from interview_engine.question_config import ConfigStore
        self.quality_store=ConfigStore(__import__('os').environ.get('INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG') or self.base/'question_quality.json')
        self.supervisor=ChildSupervisor(command or [sys.executable,'-m','delivery_api.child_entry'])
    @contextmanager
    def _registry(self):
        from .db_pool import pool_for
        from .tenancy import current_scope
        from interview_engine.tenant_worker import pg_session_store,_owner,_session
        from interview_engine.http_service import SessionRegistry
        scope=current_scope();store=pg_session_store(pool_for(self.dsn),scope.owner_id,scope.session_id)
        registry=SessionRegistry(None,session_store=store,owner_id=scope.owner_id)
        owner=_owner.set(scope.owner_id);auth=_session.set(scope.session_id)
        try:yield registry
        finally:registry.close();_session.reset(auth);_owner.reset(owner)
    def local_models(self):
        import httpx
        endpoint=self.endpoint.rsplit('/chat/completions',1)[0]+'/models'
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=3) as client:
            result=client.get(endpoint);result.raise_for_status()
            return [entry['id'] for entry in result.json()['data'] if isinstance(entry,dict) and isinstance(entry.get('id'),str)]
    def default_route(self):
        from interview_engine import session_models
        route,model=session_models.initial_default()
        if route!='ollama' and self.paid_runtime is None:raise PermissionError('local child default must be Ollama')
        if hasattr(self.gateway_factory,'for_task'):model=self.gateway_factory.models.get(route)
        return self.preview_route(route,model)
    def preview_route(self,route,initial_model=None):
        if route!='ollama' and self.paid_runtime is None:raise PermissionError('paid child route remains refused')
        from interview_engine import session_models
        if hasattr(self.gateway_factory,'for_task') and initial_model is None:
            initial_model=self.gateway_factory.models.get(route)
        from interview_engine.errors import UserFacingError
        try:
            models=session_models.resolve(route,initial_model)
            session_models.validate(models,route)
        except UserFacingError as exc:
            from .engine_client import EngineClientError
            from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
            raise EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail=exc.render())) from exc
        if self.paid_runtime is not None:
            for provider,model in models.values():self.paid_runtime.require(provider,model)
        return {'route':route,'model_map':models}
    def question_quality_config(self):
        from interview_engine import question_config
        return {'config_version':question_config.CONFIG_VERSION,'config':self.quality_store.get(),'defaults':question_config.defaults()}
    def save_question_quality_config(self,config):
        from interview_engine import question_config
        from .engine_client import QualityConfigRejected
        try:saved=self.quality_store.put(config)
        except question_config.ConfigError as exc:raise QualityConfigRejected(str(exc)) from exc
        return {'config_version':question_config.CONFIG_VERSION,'config':saved}
    def snapshot(self,sid):
        with self._registry() as registry:return registry.get(sid,serve=False).snapshot()
    def progress(self,sid,after=0):
        with self._registry() as registry:return registry.progress(sid,after)
    def _stopped_snapshot(self,sid,*,reconcile=False):
        # Fresh parent registries never own the vanished child's writer claim.
        # Only durable parent reaping + closed jobs/providers can replace it.
        with self._registry() as registry:
            snapshot=registry.stop(sid) if not reconcile else registry.snapshot(sid)
        if self.store.confirm_child_session_quiescent(sid):
            with self._registry() as registry:return registry.snapshot(sid)
        return snapshot
    def stop(self,sid):return self._stopped_snapshot(sid)
    def reconcile_stop(self,sid):return self._stopped_snapshot(sid,reconcile=True)
    def switch_route(self,sid,request):
        from .child_operation_refs import ChildOperationRefs
        from requi_child_contract import decode_operation
        from interview_engine.child_step import published_digest
        from vmodel_contracts.interactive import ModelRouteSwitchResponse,EventType,ProtocolError,ProtocolErrorCode,ProcessStatus,NextStep
        from .engine_client import EngineClientError
        def conflict(message):raise EngineClientError(409,ProtocolError(code=ProtocolErrorCode.ROUTE_SWITCH_CONFLICT,detail=message))
        if request.model_route!='ollama' and self.paid_runtime is None:raise PermissionError('paid child route remains refused')
        from .tenancy import current_scope
        scope=current_scope()
        with self._registry() as registry:
            row=registry._store.load_published(sid,scope.owner_id)
            session=registry.get(sid,serve=False);snapshot=session.snapshot()
            if (row.get('control') or {}).get('stopped'):conflict('stopped session route changes disabled')
            for item in session.route_history:
                if item.idempotency_key==request.idempotency_key:
                    if (item.to_route!=request.model_route or item.to_model_map['probing'][1]!=request.model or item.actor_id!=request.actor_id):
                        conflict('idempotency key reused with different request')
                    return ModelRouteSwitchResponse(snapshot=snapshot,accepted_event=registry._event(session,EventType.ROUTE_CHANGED,route_change=item,sequence=item.session_sequence))
        if session.status!=ProcessStatus.WAITING_INPUT or snapshot.open_input is None or snapshot.next_step!=NextStep.WAIT_INPUT:
            conflict('session is not at a stable input boundary')
        if snapshot.sequence!=request.expected_sequence or snapshot.open_input.input_id!=request.expected_input_id:
            conflict('input or session sequence changed; refresh and retry')
        source={tuple(pair) for pair in (snapshot.model_map or {}).values()}
        if len(source)!=1:raise PermissionError('one pinned source route required')
        if self.paid_runtime is None and next(iter(source))[0]!='ollama':raise PermissionError('local source route required')
        if self.paid_runtime is not None:self.paid_runtime.require(request.model_route,request.model)
        provider,model=next(iter(source));tid=snapshot.task_id
        if request.model==model:conflict('session already uses this model')
        refs=ChildOperationRefs(self.store,tid);ref=refs.put('route',request.model_dump(mode='json'))
        job=self.store.enqueue_worker(tid,'phase:intent',execution_options={'model_route':provider+':'+model})
        binding=self.store.session_binding(sid)
        value={'version':1,'operation_id':job,'binding':{'owner_id':scope.owner_id,'auth_session_id':scope.session_id,
            'task_id':tid,'session_id':sid,'job_id':job,'generation':binding['generation'],'lease_epoch':binding['lease_epoch'],
            'provider':provider,'model':model},'operation':{'kind':'interview.route','checkpoint_sha256':published_digest(row),
                'expected_sequence':snapshot.sequence,'wait_id':str(snapshot.open_input.input_id),'request_ref':ref}}
        if binding['generation']>1:value['binding']['execution_anchor_sha256']=self.store.installed_execution_anchor(sid)['anchor_sha256']
        op=decode_operation(json.dumps(value).encode()+b'\n')
        self.store.retain_decision_review(tid,'child-job:'+job,'child-operation-v1',value)
        self._launch(op,ref,'route')
        with self._registry() as registry:
            session=registry.get(sid,serve=False);payload=session.route_history[-1]
            return ModelRouteSwitchResponse(snapshot=session.snapshot(),accepted_event=registry._event(session,EventType.ROUTE_CHANGED,route_change=payload))
    def close(self):pass
    def start(self,request):
        from vmodel_contracts.interactive import StartSessionResponse
        if request.model_route!='ollama' and self.paid_runtime is None:raise PermissionError('paid child dispatch remains refused')
        if not request.session_id:request=request.model_copy(update={'session_id':uuid4()})
        self._execute(str(request.session_id),request=request)
        with self._registry() as registry:
            from vmodel_contracts.interactive import EventType
            session=registry.get(str(request.session_id),serve=False)
            return StartSessionResponse(snapshot=session.snapshot(),accepted_event=registry._event(session,EventType.RUN_STARTED))
    def submit(self,sid,response):
        from vmodel_contracts.interactive import SubmitInputResponse,EventType
        self._execute(sid,response=response)
        with self._registry() as registry:
            session=registry.get(sid,serve=False)
            return SubmitInputResponse(snapshot=session.snapshot(),accepted_event=registry._event(session,EventType.INPUT_ACCEPTED))
    def _execute(self,sid,*,request=None,response=None):
        from .tenancy import current_scope
        from .child_operation_refs import ChildOperationRefs
        from requi_child_contract import decode_operation
        from interview_engine.child_step import published_digest
        scope=current_scope();binding=self.store.session_binding(sid)
        if not binding:raise PermissionError('prebound engine session required')
        task_id=binding['task_id'];refs=ChildOperationRefs(self.store,task_id)
        if request is not None:
            provider=request.model_route
            # Preview and dispatch must use the same reviewed task pin. The
            # local-only runtime has no PaidChildRuntime; that is not permission
            # to substitute an old model default after preflight succeeded.
            if hasattr(self.gateway_factory,'for_task'):
                reviewed=self.gateway_factory.for_task(task_id).models.get(provider)
                if not reviewed or (request.initial_model is not None and request.initial_model!=reviewed):
                    raise PermissionError('child Start differs from reviewed task model')
                model=reviewed
            else:
                model=request.initial_model or (self.paid_runtime.models.get(provider) if self.paid_runtime is not None else 'qwen2.5-coder:14b')
            if self.paid_runtime is not None:self.paid_runtime.require(provider,model)
            request=request.model_copy(update={'initial_model':model})
            ref=refs.put('start',request.model_dump(mode='json'))
            body={'kind':'interview.start','request_ref':ref}
        else:
            with self._registry() as registry:
                row=registry._store.load_published(sid,scope.owner_id)
                snapshot=registry.get(sid,serve=False).snapshot()
            if not snapshot.open_input:raise PermissionError('no open child input')
            pairs={tuple(pair) for pair in (snapshot.model_map or {}).values()}
            if len(pairs)!=1:raise PermissionError('unpinned child answer refused')
            if self.paid_runtime is None and next(iter(pairs))[0]!='ollama':raise PermissionError('paid child answer refused')
            provider,model=next(iter(pairs))
            if hasattr(self.gateway_factory,'for_task'):self.gateway_factory.for_task(task_id)
            if self.paid_runtime is not None:self.paid_runtime.require(provider,model)
            # The HTTP contract permits an omitted sequence. Pin both the
            # durable answer and its operation to the same published revision.
            # Never replace an explicit caller precondition.
            if response.expected_sequence is None:
                response=response.model_copy(update={'expected_sequence':snapshot.sequence})
            ref=refs.put('answer',response.model_dump(mode='json'))
            body={'kind':'interview.answer','checkpoint_sha256':published_digest(row),
                'wait_id':str(snapshot.open_input.input_id),'expected_sequence':snapshot.sequence,
                'review_revision':snapshot.open_input.context.get('evidence_revision') or 'engine:'+sid+':'+str(snapshot.sequence),'answer_ref':ref}
        if request is not None and binding['lease_epoch']==0:
            with self._registry() as registry:
                claim=registry._store.claim(sid,scope.owner_id,'child-bootstrap#'+str(uuid4()),30)
                if claim is None:raise PermissionError('bootstrap lease held')
                if not registry._store.release(claim):raise PermissionError('bootstrap release lost')
        options={'model_route':provider+':'+model}
        job_id=self.store.enqueue_worker(task_id,'phase:intent',execution_options=options)
        # The durable job id is the logical operation id, reused by recovery.
        binding=self.store.session_binding(sid)
        value={'version':1,'operation_id':job_id,'binding':{'owner_id':scope.owner_id,'auth_session_id':scope.session_id,
            'task_id':task_id,'session_id':sid,'job_id':job_id,'generation':binding['generation'],
            'lease_epoch':binding['lease_epoch'],'provider':provider,'model':model},'operation':body}
        if binding['generation']>1:value['binding']['execution_anchor_sha256']=self.store.installed_execution_anchor(sid)['anchor_sha256']
        operation=decode_operation(json.dumps(value).encode()+b'\n')
        self.store.retain_decision_review(task_id,'child-job:'+job_id,'child-operation-v1',value)
        self._launch(operation,ref,'start' if request is not None else 'answer')

    def resume(self,job_id):
        """Explicit owner recovery, never automatic retry or a new operation id."""
        from .tenancy import current_scope
        from .child_operation_refs import ChildOperationRefs
        from requi_child_contract import decode_operation
        from interview_engine.child_step import published_digest
        scope=current_scope();job=self.store.worker_job(job_id)
        if not job:raise PermissionError('recoverable owner job required')
        value=self.store.decision_review(job['task_id'],'child-job:'+job_id,'child-operation-v1')
        if not value or value['binding']['session_id']!=job['session_id']:raise PermissionError('durable child operation missing')
        row=self.store.session_binding(job['session_id']);control=row.get('control') or {}
        if row['lease_expires_at']>__import__('time').time():raise PermissionError('prior child lease still live; wait for expiry')
        if control.get('stopped') or row['generation']!=job['generation']:raise PermissionError('stopped or changed generation cannot resume')
        body=value['operation'];kind={'interview.start':'start','interview.route':'route'}.get(body['kind'],'answer')
        ref=body.get('request_ref') or body.get('answer_ref')
        self.store.reauthorize_worker(job_id)
        # A committed answer/start is completion recovery, not graph replay.
        committed=control.get('sequence',0)>(1 if kind=='start' else body['expected_sequence'])
        if committed:
            completed=self.store.decision_review(job['task_id'],'child-job:'+job_id,'child-completion-v1')
            if completed is None:raise PermissionError('published result exists but exact completion evidence missing; reconcile without replay')
            capability=self.store.claim_worker(job_id)
            if capability is None:raise PermissionError('recovery job already claimed')
            capability.validate(self.store);self.store.finish_worker(job_id)
            return self.snapshot(job['session_id'])
        value['binding'].update(auth_session_id=scope.session_id,lease_epoch=row['lease_epoch'])
        if kind=='start' and control:
            if not row.get('published_checkpoint_id'):
                raise PermissionError('interrupted bootstrap has no published checkpoint; reconcile without replay')
            body={'kind':'interview.advance','checkpoint_sha256':published_digest(row),'expected_sequence':control['sequence']}
            value['operation']=body
        operation=decode_operation(json.dumps(value).encode()+b'\n')
        self.store.retain_decision_review(job['task_id'],'child-job:'+job_id,'child-recovery:'+str(row['lease_epoch']),value)
        self._launch(operation,ref,kind)
        return self.snapshot(job['session_id'])

    def _launch(self,operation,ref,ref_kind):
        from .tenancy import current_scope
        from .child_operation_refs import ChildOperationRefs
        scope=current_scope();sid=operation.binding.session_id;task_id=operation.binding.task_id;job_id=operation.binding.job_id
        refs=ChildOperationRefs(self.store,task_id)
        quiescent=False;timings={};slot_at=time.monotonic()
        async def run():
            run_at=time.monotonic()
            from .tenant_jobs import execute_job
            async def handler(capability):
                nonlocal quiescent
                setup_at=time.monotonic()
                from .child_provider_broker import ChildProviderBroker
                from .child_broker_authority import ChildBrokerAuthority
                from .child_broker_journal import ChildBrokerJournal
                from .child_broker_channel import provider_broker_channel
                from .child_compute_channel import child_compute_channel
                from .tenancy import session_root
                import httpx
                gateway=self.gateway_factory(capability,job_id)
                if getattr(gateway,'retail_rates',None) is not None and self.paid_runtime is None:raise PermissionError('paid child dispatch remains refused')
                journal=ChildBrokerJournal(self.store,operation)
                compute=LocalComputeEvidence(lambda value:self.store.retain_decision_review(task_id,'child-job:'+job_id,'compute:'+str(operation.binding.lease_epoch),value))
                if self.paid_runtime is not None:compute=self.paid_runtime.compute(self.store,operation,gateway,'intent',journal) or compute
                endpoint,headers=self.paid_runtime.transport(operation.binding.provider,operation.binding.model,self.endpoint) if self.paid_runtime is not None else (self.endpoint,{})
                authority=ChildBrokerAuthority(self.store,operation,lease_epoch=operation.binding.lease_epoch+1)
                root=session_root(self.base,scope,sid);owner_root=root.parent
                cfg={'version':1,'provider':operation.binding.provider,'binding':operation.binding.model_dump(mode='json'),
                    'dsn':self.dsn,'owner_root':str(owner_root),'input_ref':ref,
                    'input':refs.get(ref,ref_kind),'question_quality':self.question_quality_config()['config']}
                stop=asyncio.Event()
                async def watch():
                    while not stop.is_set():
                        task=self.store.task(task_id);session=self.store.session_binding(sid)
                        if (not task or task.get('stop_requested_at') is not None or not self.store.auth_session_active()
                            or not session or session['generation']!=operation.binding.generation):stop.set();return
                        await asyncio.sleep(1)
                watcher=asyncio.create_task(watch())
                def fence_stop(op):
                    task=self.store.task(task_id);session=self.store.session_binding(sid)
                    if task and task.get('stop_requested_at') is None and session and session['generation']==op.binding.generation:
                        raise PermissionError('stop signal has no durable stop/revocation evidence')
                child_error=None
                with httpx.Client(trust_env=False,follow_redirects=False,timeout=None) as client:
                    broker=ChildProviderBroker(operation,gateway=gateway,journal=journal,client=client,
                        endpoint=endpoint,headers=headers,authorize=authority)
                    from .child_progress import progress_wake_channel
                    with provider_broker_channel(broker) as provider_fd,child_compute_channel(compute) as compute_fd,progress_wake_channel(sid) as progress_fd:
                        timings['setup']=int((time.monotonic()-setup_at)*1000);supervisor_at=time.monotonic()
                        try:
                            await self.supervisor.run(operation,authorize=lambda op:capability.validate(self.store),private_config=cfg,
                                descriptors={'broker':provider_fd.fileno(),'compute':compute_fd.fileno(),'progress':progress_fd.fileno()},stop=stop,persist_stop=fence_stop)
                        except Exception as error:child_error=error
                timings['supervisor']=int((time.monotonic()-supervisor_at)*1000);completion_at=time.monotonic()
                watcher.cancel()
                try:await watcher
                except asyncio.CancelledError:pass
                task_after=self.store.task(task_id)
                stopped_by_owner=bool(task_after and task_after.get('stop_requested_at') is not None)
                if stopped_by_owner:
                    if self.supervisor.last_result is None or not self.supervisor.last_result.reaped:
                        raise RuntimeError('stopped child reaping unverified')
                    self.store.retain_decision_review(task_id,'child-job:'+job_id,'child-completion-v1',
                        {'operation_id':operation.operation_id,'compute_ms':str(compute.value) if compute.settled else None,'compute_settled':compute.settled,'reaped':True,'stopped':True})
                    quiescent=True
                    raise RuntimeError('child stopped after reap and channel quiescence') from child_error
                if not compute.settled:raise RuntimeError('child exact compute evidence incomplete')
                if child_error is not None:raise child_error
                # The child commits canonical PG documents, not delivery's ready
                # artifact manifest. Publish the terminal set while the original
                # scoped job is still running, after reap and exact accounting.
                from vmodel_contracts.interactive import ProcessStatus
                if self.snapshot(sid).process_status==ProcessStatus.COMPLETED:
                    from .generator_documents import GeneratorDocuments
                    from .artifact_publication import publish_document_revision
                    documents=GeneratorDocuments(self.store,capability,job_id,
                        expected_attempt=self.store.worker_job(job_id)['attempt'])
                    publish_document_revision(self.store,task_id,documents,
                        writer_instance='interview-child:'+operation.operation_id)
                timings['completion']=int((time.monotonic()-completion_at)*1000);timings['run']=int((time.monotonic()-run_at)*1000)
                self.store.retain_decision_review(task_id,'child-job:'+job_id,'child-completion-v1',
                    {'operation_id':operation.operation_id,'compute_ms':str(compute.value),'reaped':True,'timings':timings,'provider_timings':broker.timings})
                quiescent=True
                self.store.add_event(task_id,'child.completed','Local child reaped after durable publication',
                    payload={'job_id':job_id,'compute_ms':str(compute.value),'pid':self.supervisor.last_result.pid})
            return await execute_job(self.store,job_id,handler,
                verify_completion_quiescence=lambda cap:quiescent,verify_stopped_quiescence=lambda cap:quiescent)
        with _slot:
            timings['slot_wait']=int((time.monotonic()-slot_at)*1000)
            try:asyncio.run(run())
            except BaseException:
                try:self.store.pause_interrupted_worker(job_id)
                except Exception:
                    from .diagnostic_evidence import failure
                    failure('child.pause.failed')
                raise


class ChildInterviewHost:
    def __init__(self,store,base,*,gateway_factory,session_dsn,endpoint,paid_runtime=None):
        if not session_dsn:raise ValueError('child interview requires explicit Postgres session storage')
        self._client=ChildInterviewClient(store,base,gateway_factory=gateway_factory,dsn=session_dsn,endpoint=endpoint,paid_runtime=paid_runtime)
    def client(self):return self._client
    def close(self):self._client.close()
