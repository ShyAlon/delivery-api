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

# The stop/auth/generation watch lives in stop_watch.py: a Stop click wakes it at once, revocation is read every
# 30 s. Pinned by tests/test_child_stop_watch_cadence.py and tests/test_stop_watch_wake.py.

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
    def __init__(self,store,artifacts_base,*,gateway_factory,dsn,endpoint,command=None,paid_runtime=None,answer_accounting=None):
        if paid_runtime is not None:
            from .child_paid_runtime import PaidChildRuntime
            if not isinstance(paid_runtime,PaidChildRuntime):raise TypeError('typed parent accounting runtime required')
        if answer_accounting is not None:
            from .answer_runtime import AnswerAccountingMode
            if not isinstance(answer_accounting,AnswerAccountingMode):raise TypeError('typed answer accounting mode required')
            if paid_runtime is None:raise ValueError('answer accounting requires paid parent transport')
        self.answer_accounting=answer_accounting
        self.paid_runtime=paid_runtime
        self.store=store;self.base=Path(artifacts_base);self.gateway_factory=gateway_factory;self.dsn=dsn;self.endpoint=endpoint
        from interview_engine.question_config import ConfigStore
        self.quality_store=ConfigStore(__import__('os').environ.get('INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG') or self.base/'question_quality.json')
        self.supervisor=ChildSupervisor(command or [sys.executable,'-m','delivery_api.child_entry'],timing_sink=self._record_engine_timing,timing_batch_sink=self._record_engine_timings)
        # Parked warm children keyed by session id: a verified completed answer
        # on a session waiting for the next input keeps its claimed child alive.
        self._warm={}
        # One persistent supervisor loop: a parked warm child's process and
        # stream transports are loop-bound, so every supervisor interaction
        # (spawn, follow-up, reap, recycle) must run on the loop that spawned it.
        self._loop=None;self._loop_thread=None;self._loop_lock=threading.Lock()

    def _run(self,coro):
        with self._loop_lock:
            if self._loop is None:
                loop=asyncio.new_event_loop()
                thread=threading.Thread(target=loop.run_forever,daemon=True,name='child-supervisor-loop')
                thread.start();self._loop=loop;self._loop_thread=thread
        return asyncio.run_coroutine_threadsafe(coro,self._loop).result()

    def _drop_warm(self,sid,*,recycle=False):
        entry=self._warm.pop(sid,None)
        if entry is None:return
        def parked_fence(op):
            task=self.store.task(entry['operation'].binding.task_id);session=self.store.session_binding(sid)
            if task and task.get('stop_requested_at') is None and session and session['generation']==op.binding.generation:
                raise PermissionError('stop signal has no durable stop/revocation evidence')
        async def finish():
            if recycle:await self.supervisor.close_parked(entry['handle'],entry['operation'])
            else:await self.supervisor.stop_parked(entry['handle'],entry['operation'],persist_stop=parked_fence)
        try:self._run(finish())
        except RuntimeError:pass  # a stopped loop owns teardown; process exit kills the group
        ctx=entry.get('progress_ctx')
        if ctx is not None:ctx.__exit__(None,None,None)
    def _record_engine_timing(self,session_id,stage,label,status):
        task_id=self.store.task_for_engine_session(session_id)
        if not task_id:return
        self.store.add_event(task_id,"progress",label,phase_key=None,payload={"stage":stage,"status":status,"source":"engine.timing"})

    def _record_engine_timings(self,entries):
        return self.store.add_engine_timing_batch(entries)

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
        if not reconcile:self._drop_warm(sid)  # the stop fence lands first; reap the parked child after
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
        self._drop_warm(sid,recycle=True)  # a route switch recycles any parked child
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
    def drop_task_sessions(self,task_id):
        # Purge/delete teardown: recycle (never fence-stop) parked children
        # whose operation belongs to the task; their exit releases the claims
        # the purge checks for.
        for sid,entry in list(self._warm.items()):
            if entry['operation'].binding.task_id==task_id:self._drop_warm(sid,recycle=True)

    def close(self):
        for sid in list(self._warm):self._drop_warm(sid,recycle=True)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=10)
            self._loop.close();self._loop=None;self._loop_thread=None
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
        # Our own parked child only idles on this session's claim; recycle it
        # so the liveness check below measures genuinely foreign children.
        self._drop_warm(job['session_id'],recycle=True)
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
        from requi_child_contract import encode_operation
        scope=current_scope();sid=operation.binding.session_id;task_id=operation.binding.task_id;job_id=operation.binding.job_id
        refs=ChildOperationRefs(self.store,task_id)
        quiescent=False;timings={};slot_at=time.monotonic()
        # A parked warm child serves this session's next answer; recycle it
        # when it has been idle past the policy window.
        parked_entry=self._warm.get(sid)
        if ref_kind!='answer' or (parked_entry is not None and time.monotonic()-parked_entry['parked_at']>600):
            if parked_entry is not None:self._drop_warm(sid,recycle=True)
            parked_entry=None
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
                answer_run=None
                if self.answer_accounting is not None and ref_kind=='answer' and operation.binding.provider=='deepseek':
                    answer_run=self.answer_accounting.begin(self.store,operation,refs.get(ref,ref_kind),gateway)
                journal=None if answer_run is not None else ChildBrokerJournal(self.store,operation)
                compute=LocalComputeEvidence(lambda value:self.store.retain_decision_review(task_id,'child-job:'+job_id,'compute:'+str(operation.binding.lease_epoch),value))
                if answer_run is not None:
                    from .answer_runtime import AnswerComputeAccounting
                    compute=AnswerComputeAccounting(answer_run)
                elif self.paid_runtime is not None:compute=self.paid_runtime.compute(self.store,operation,gateway,'intent',journal) or compute
                endpoint,headers=self.paid_runtime.transport(operation.binding.provider,operation.binding.model,self.endpoint) if self.paid_runtime is not None else (self.endpoint,{})
                # Cold dispatch authorizes the epoch the child will claim (row
                # epoch + 1); a warm dispatch authorizes the epoch the parked
                # child already holds (the row's live lease).
                authority=ChildBrokerAuthority(self.store,operation,
                    lease_epoch=operation.binding.lease_epoch+(0 if parked_entry is not None else 1))
                root=session_root(self.base,scope,sid);owner_root=root.parent
                cfg={'version':1,'provider':operation.binding.provider,'binding':operation.binding.model_dump(mode='json'),
                    'dsn':self.dsn,'owner_root':str(owner_root),'input_ref':ref,
                    'input':refs.get(ref,ref_kind),'question_quality':self.question_quality_config()['config'],
                    'warm':ref_kind in ('answer','route')}
                stop=asyncio.Event()
                from .stop_watch import watch_for_stop
                watcher=asyncio.create_task(watch_for_stop(store=self.store,task_id=task_id,session_id=sid,generation=operation.binding.generation,stop=stop))
                def fence_stop(op):
                    task=self.store.task(task_id);session=self.store.session_binding(sid)
                    if task and task.get('stop_requested_at') is None and session and session['generation']==op.binding.generation:
                        raise PermissionError('stop signal has no durable stop/revocation evidence')
                if parked_entry is not None:
                    self.store.add_event(task_id,'progress','Reusing an existing session',phase_key=None,
                        payload={'stage':'engine.session','status':'started','source':'engine.timing'})
                    def on_event(event):
                        if event.state=='running':
                            self.store.add_event(task_id,'progress','Reusing an existing session',phase_key=None,
                                payload={'stage':'engine.session','status':'completed','source':'engine.timing'})
                else:
                    self.store.add_event(task_id,'progress','Warming up a fresh session',phase_key=None,
                        payload={'stage':'engine.session','status':'started','source':'engine.timing'})
                    on_event=lambda event:None
                child_error=None;handle=None;progress_ctx=None
                def drop_entry():
                    # The handle is reaped by the handler's own code; this only
                    # unregisters and closes the parked progress channel.
                    popped=self._warm.pop(sid,None)
                    if popped is not None and popped.get('progress_ctx') is not None:
                        popped['progress_ctx'].__exit__(None,None,None)
                with httpx.Client(trust_env=False,follow_redirects=False,timeout=None) as client:
                    if answer_run is not None:
                        from .answer_runtime import AnswerProviderBroker
                        broker=AnswerProviderBroker(answer_run,provider=operation.binding.provider,model=operation.binding.model,
                            client=client,endpoint=endpoint,headers=headers,stop=stop)
                    else:
                        broker=ChildProviderBroker(operation,gateway=gateway,journal=journal,client=client,
                            endpoint=endpoint,headers=headers,authorize=authority)
                    from .child_progress import progress_wake_channel
                    with provider_broker_channel(broker) as provider_fd,child_compute_channel(compute) as compute_fd:
                        if parked_entry is None:
                            progress_ctx=progress_wake_channel(sid);progress_fd=progress_ctx.__enter__()
                        timings['setup']=int((time.monotonic()-setup_at)*1000);supervisor_at=time.monotonic()
                        try:
                            if parked_entry is not None:
                                handle=parked_entry['handle']
                                await self.supervisor.run_operation(handle,operation,on_event=on_event,
                                    payload={'version':1,'binding':cfg['binding'],'operation':json.loads(encode_operation(operation).decode()),
                                        'input':cfg['input'],'input_ref':cfg['input_ref'],'fds':{'broker':0,'compute':1}},
                                    fds=[provider_fd.fileno(),compute_fd.fileno()],stop=stop,persist_stop=fence_stop,park=True)
                            else:
                                handle=await self.supervisor.spawn(operation,authorize=lambda op:capability.validate(self.store),
                                    private_config=cfg,descriptors={'broker':provider_fd.fileno(),'compute':compute_fd.fileno(),'progress':progress_fd.fileno()})
                                await self.supervisor.run_operation(handle,operation,on_event=on_event,first=True,park=bool(cfg['warm']),
                                    stop=stop,persist_stop=fence_stop)
                            if handle.state!='completed':
                                # Stopped: verify EOF/exit, then reap so the
                                # stopped-by-owner path sees a reaped result.
                                # Failed: finish_terminal raises.
                                await self.supervisor.finish_terminal(handle)
                                await self.supervisor.reap(handle,operation)
                                drop_entry();handle=None
                        except Exception as error:child_error=error
                # Both descriptor receivers have drained before the one correction.
                # Unknown provider/compute evidence retains E, never fabricates a refund.
                if answer_run is not None:answer_run.finish()
                timings['supervisor']=int((time.monotonic()-supervisor_at)*1000);completion_at=time.monotonic()
                watcher.cancel()
                try:await watcher
                except asyncio.CancelledError:pass
                if child_error is not None and handle is not None:
                    await self.supervisor.reap(handle,operation,failed=True,failure_error=child_error)
                    drop_entry();handle=None
                task_after=self.store.task(task_id)
                stopped_by_owner=bool(task_after and task_after.get('stop_requested_at') is not None)
                if stopped_by_owner:
                    if self.supervisor.last_result is None or not self.supervisor.last_result.reaped:
                        raise RuntimeError('stopped child reaping unverified')
                    self.store.retain_decision_review(task_id,'child-job:'+job_id,'child-completion-v1',
                        {'operation_id':operation.operation_id,'compute_ms':str(compute.value) if compute.settled else None,'compute_settled':compute.settled,'reaped':True,'stopped':True})
                    quiescent=True
                    raise RuntimeError('child stopped after reap and channel quiescence') from child_error
                try:
                    if not compute.settled:raise RuntimeError('child exact compute evidence incomplete')
                    if child_error is not None:raise child_error
                    # The child commits canonical PG documents, not delivery's ready
                    # artifact manifest. Publish the terminal set while the original
                    # scoped job is still running, after reap and exact accounting.
                    from vmodel_contracts.interactive import ProcessStatus
                    process_status=self.snapshot(sid).process_status
                    if process_status==ProcessStatus.COMPLETED:
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
                    # Parking decision: a verified completed operation on a
                    # warm-capable child whose session waits for the next input
                    # keeps its claimed child alive.
                    parking=(handle is not None and handle.state=='completed'
                        and (parked_entry is not None or cfg['warm']) and process_status==ProcessStatus.WAITING_INPUT)
                    self.store.add_event(task_id,'child.completed',
                        'Local child parked after durable publication' if parking else 'Local child reaped after durable publication',
                        payload={'job_id':job_id,'compute_ms':str(compute.value),'pid':handle.process.pid if handle is not None else self.supervisor.last_result.pid})
                    if parking:
                        ctx=progress_ctx if parked_entry is None else parked_entry['progress_ctx']
                        self._warm[sid]={'handle':handle,'operation':operation,'progress_ctx':ctx,'parked_at':time.monotonic()}
                        progress_ctx=None;handle=None
                    if handle is not None:
                        if cfg['warm']:await self.supervisor.close_parked(handle,operation)
                        else:await self.supervisor.reap(handle,operation)
                        handle=None
                    if progress_ctx is not None:progress_ctx.__exit__(None,None,None);progress_ctx=None
                except BaseException:
                    if handle is not None:
                        if handle.state=='completed' and child_error is None:
                            try:await self.supervisor.close_parked(handle,operation)
                            except Exception:pass
                        else:await self.supervisor.reap(handle,operation,failed=True,failure_error=child_error)
                    drop_entry()
                    if progress_ctx is not None:progress_ctx.__exit__(None,None,None)
                    raise
            return await execute_job(self.store,job_id,handler,
                verify_completion_quiescence=lambda cap:quiescent,verify_stopped_quiescence=lambda cap:quiescent)
        with _slot:
            timings['slot_wait']=int((time.monotonic()-slot_at)*1000)
            try:self._run(run())
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
