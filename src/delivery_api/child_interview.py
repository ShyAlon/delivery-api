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
        self.supervisor=ChildSupervisor(command or [sys.executable,'-m','delivery_api.child_entry'],timing_sink=self._record_engine_timing,timing_batch_sink=self._record_engine_timings,trace_sink=self._record_answer_trace,progress_sink=self._record_answer_progress,result_sink=self._record_answer_result)
        # Parked warm children keyed by session id: a verified completed answer
        # on a session waiting for the next input keeps its claimed child alive.
        self._warm={}
        self._answer_traces={}
        self._answer_trace_ready={}
        self._active_answers={}
        self._answer_snapshots={}
        self._answer_results={}
        self._transient_progress={}
        self._progress_operations={}
        self._progress_lock=threading.Lock()
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
    def _record_answer_trace(self,row):
        if not isinstance(row,dict) or set(row)-{'operation_id','caller','waits','syncs','duration_ms','complete'}:return
        op=row.get('operation_id')
        if not isinstance(op,str) or op not in self._answer_traces:return
        value=self._answer_traces[op]
        if 'complete' in row:
            if type(row['complete']) is not bool:return
            value['complete']=row['complete']
            self._answer_trace_ready[op].set();return
        if not isinstance(row.get('caller'),str) or len(row['caller'])>300:return
        if any(type(row.get(k)) not in (int,float) or row[k]<0 for k in ('waits','syncs','duration_ms')):return
        value['callers'][row['caller']]={k:row[k] for k in ('waits','syncs','duration_ms')}

    def _record_answer_result(self,row):
        if not isinstance(row,dict):return
        result=self._answer_results.get(row.get('operation_id'))
        if result is not None:result.accept(row)

    def _record_answer_progress(self,row):
        from vmodel_contracts.progress_signal import ProgressSignal
        if not isinstance(row,dict) or set(row)!={'operation_id','page'}:return
        target=self._progress_operations.get(row['operation_id'])
        if target is None:return
        page={k:v for k,v in row['page'].items() if k!='journal_mode'}
        signal=ProgressSignal.model_validate(page)
        if signal.session_id!=target['sid'] or signal.task_id!=target['task_id']:return
        with self._progress_lock:
            target['cursor']+=1
            target['events'].append(signal.model_copy(update={'cursor':target['cursor']}))
        from . import event_bus
        event_bus.notify('engine-progress:'+target['sid'])

    def _record_engine_timing(self,session_id,stage,label,status):
        task_id=self.store.task_for_engine_session(session_id)
        if not task_id:return
        self.store.add_event(task_id,"progress",label,phase_key=None,payload={"stage":stage,"status":status,"source":"engine.timing"})

    def _record_engine_timings(self,entries):
        legacy=[]
        with self._progress_lock:
            active={target['sid']:target for target in self._progress_operations.values()}
            for entry in entries:
                target=active.get(entry[0])
                if target is None:legacy.append(entry)
                else:target['timings'].append(entry)
        if legacy:return self.store.add_engine_timing_batch(legacy)


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
    def answer_progress(self,sid,after=0):
        """Best-effort memory stream, never a durable engine journal cursor."""
        from .tenancy import current_scope
        from vmodel_contracts.progress_signal import ProgressPage
        with self._progress_lock:
            target=self._transient_progress.get(sid)
            if target is not None and target['scope']==current_scope():
                events=[e for e in target['events'] if e.cursor>after]
                return ProgressPage(events=events,next_cursor=events[-1].cursor if events else after)
        return None
    def progress(self,sid,after=0):
        # Public callers retain the observational stream. The delivery watcher
        # uses answer_progress explicitly, keeping its cursor out of PG ingest.
        page=self.answer_progress(sid,after)
        if page is not None:return page
        return self.durable_progress(sid,after)
    def durable_progress(self,sid,after=0):
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
        if self.answer_accounting is None:
            self._execute(sid,response=response)
            with self._registry() as registry:
                session=registry.get(sid,serve=False)
                return SubmitInputResponse(snapshot=session.snapshot(),accepted_event=registry._event(session,EventType.INPUT_ACCEPTED))
        from .postgres_scope import RequestStats,request_stats
        from .answer_trace import tables,flush,WireTrace
        stats=request_stats.get();token=None
        if stats is None:stats=RequestStats();token=request_stats.set(stats)
        first=len(stats.trace);began=time.monotonic();error=None
        from .db_pool import answer_pool_reuse
        pool_token=answer_pool_reuse.set(True)
        wire=WireTrace();wire.install(context_only=True)
        from .postgres_store import answer_admission
        admission=answer_admission(self.store);admission.__enter__()
        try:
            if not self.answer_accounting.replay(self.store,sid,response):self._execute(sid,response=response)
            completion=self._answer_snapshots.pop(sid,None)
            if completion is not None:return SubmitInputResponse(snapshot=completion[0],accepted_event=completion[1])
            with self._registry() as registry:
                session=registry.get(sid,serve=False)
                return SubmitInputResponse(snapshot=session.snapshot(),accepted_event=registry._event(session,EventType.INPUT_ACCEPTED))
        except BaseException as exc:
            error=type(exc).__name__;raise
        finally:
            self._answer_snapshots.pop(sid,None)
            run=self._active_answers.pop(sid,None)
            if run is not None:
                try:run.finish()
                except Exception:pass  # Preserve original error; receipt remains the durable accounting truth.
                wire.restore()  # Include even fallback correction in the whole-answer trace.
                job=run.receipt['job_id'];child=self._answer_traces.pop(job,None)
                parent=tables(stats.trace[first:])
                parent_wire=wire.snapshot()
                parent=tables([(row['caller'],'wire wait',row['duration_ms'],row['syncs']) for row in parent_wire])
                self._last_answer_trace={'parent_wire':parent_wire,'parent_wire_complete':not wire.failed,'job_id':job,'session_id':sid,'duration_ms':round((time.monotonic()-began)*1000),
                    'parent':parent,'child':child,'accounting':run.summary(),'error_class':error,
                    'engine_timings':list(self._progress_operations.get(job,{}).get('timings',())),
                    'total_syncs':sum(v['syncs'] for v in parent_wire)+sum(v['syncs'] for v in child['callers'].values()) if child and child['complete'] and not wire.failed else None}
                try:flush(self._last_answer_trace)
                except Exception:
                    from .diagnostic_evidence import failure
                    failure('answer.trace.flush_failed')
                self._answer_trace_ready.pop(job,None)
                self._answer_results.pop(job,None)
                self._progress_operations.pop(job,None)
            else:wire.restore()
            admission.__exit__(None,None,None)
            answer_pool_reuse.reset(pool_token)
            if token is not None:request_stats.reset(token)
    def _execute(self,sid,*,request=None,response=None):
        if self.answer_accounting is not None and response is not None:
            operation,ref,kind,frozen_input,claimed_answer=self.store.run_answer_batch(lambda:self._prepare_execute(sid,request=request,response=response))
        else:operation,ref,kind,frozen_input,claimed_answer=self._prepare_execute(sid,request=request,response=response)
        # Entry transaction has committed and its owner lock is released before
        # the child, provider, watcher or payment connection is touched.
        self._launch(operation,ref,kind,frozen_input=frozen_input,claimed_answer=claimed_answer)

    def _prepare_execute(self,sid,*,request=None,response=None):
        from .tenancy import current_scope
        from .child_operation_refs import ChildOperationRefs
        from requi_child_contract import decode_operation
        from interview_engine.child_step import published_digest
        scope=current_scope();binding=self.store.session_binding(sid)
        if not binding:raise PermissionError('prebound engine session required')
        task_id=binding['task_id']
        retain=self.store.retain_answer_review if self.answer_accounting is not None and response is not None else self.store.retain_decision_review
        refs=ChildOperationRefs(self.store,task_id,retain=retain)
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
                # session_binding already returned this exact published control
                # row under tenant RLS in the short entry transaction.
                row=binding if self.answer_accounting is not None else registry._store.load_published(sid,scope.owner_id)
                if row is None or not row.get('control'):raise PermissionError('published input required')
                payload=dict(row['control'])
                session=registry._restore_payload(payload,sid)
                snapshot=registry._read_restore(session,payload,check_legacy_pin=False).snapshot()
            if not snapshot.open_input:raise PermissionError('no open child input')
            if (str(response.input_id)!=str(snapshot.open_input.input_id)
                or (response.expected_sequence is not None and response.expected_sequence!=snapshot.sequence)):
                raise PermissionError('answer input or sequence changed before entry')
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
        answer_entry=self.answer_accounting is not None and response is not None and provider=='deepseek'
        if answer_entry:
            from .worker_capability import WorkerCapability,answer_authority
            from .stop_watch import stop_key
            from . import event_bus
            entry_stop_version=event_bus.version(stop_key(task_id))
            capability,entry_task=WorkerCapability._capture_and_validate(self.store,task_id)
            if capability.session_id!=sid or capability.generation!=binding['generation']:
                raise PermissionError('answer entry identity changed')
            with answer_authority(self.store,capability,entry_task):
                job_id=self.store.enqueue_worker(task_id,'phase:intent',execution_options=options)
        else:job_id=self.store.enqueue_worker(task_id,'phase:intent',execution_options=options)
        # The durable job id is the logical operation id, reused by recovery.
        if not answer_entry:binding=self.store.session_binding(sid)
        value={'version':1,'operation_id':job_id,'binding':{'owner_id':scope.owner_id,'auth_session_id':scope.session_id,
            'task_id':task_id,'session_id':sid,'job_id':job_id,'generation':binding['generation'],
            'lease_epoch':binding['lease_epoch'],'provider':provider,'model':model},'operation':body}
        if binding['generation']>1:value['binding']['execution_anchor_sha256']=self.store.installed_execution_anchor(sid)['anchor_sha256']
        operation=decode_operation(json.dumps(value).encode()+b'\n')
        retain(task_id,'child-job:'+job_id,'child-operation-v1',value)
        claimed_answer=None
        if answer_entry:
            with answer_authority(self.store,capability,entry_task):
                claimed=self.store.claim_worker(job_id)
            if claimed is None:raise PermissionError('answer entry job already claimed')
            claimed_answer=(claimed,entry_task,job_id,entry_stop_version)
        return operation,ref,'start' if request is not None else 'answer',response.model_dump(mode='json') if answer_entry else None,claimed_answer

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
        if self.answer_accounting is not None and kind=='answer' and value['binding']['provider']=='deepseek':
            receipt=self.answer_accounting.prior(self.store,job['session_id'],body['wait_id'])
            completed=self.store.decision_review(job['task_id'],'child-job:'+job_id,'child-completion-v1')
            committed=control.get('sequence',0)>body['expected_sequence']
            if not receipt or receipt['state']!='settled' or not completed or not committed:
                raise PermissionError('two-touch answer requires receipt/publication reconciliation; never replay provider work')
            if job['state']!='done':
                self.store.reauthorize_worker(job_id)
                capability=self.store.claim_worker(job_id)
                if capability is None:raise PermissionError('recovery job already claimed')
                capability.validate(self.store);self.store.finish_worker(job_id)
            return self.snapshot(job['session_id'])
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

    def _launch(self,operation,ref,ref_kind,*,frozen_input=None,claimed_answer=None):
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
                trace_started=time.monotonic()
                if self.answer_accounting is not None:
                    self._answer_traces[job_id]={'complete':False,'callers':{}}
                    self._answer_trace_ready[job_id]=asyncio.Event()
                    if ref_kind=='answer':
                        from .answer_result import AnswerResult
                        self._answer_results[job_id]=AnswerResult(operation)
                input_payload=refs.frozen(ref,ref_kind,frozen_input) if frozen_input is not None else refs.get(ref,ref_kind)
                answer_run=None
                if self.answer_accounting is not None and ref_kind=='answer' and operation.binding.provider=='deepseek':
                    answer_run=self.answer_accounting.begin(self.store,operation,input_payload,gateway)
                    self._active_answers[sid]=answer_run
                    from collections import deque
                    with self._progress_lock:
                        # Observability memory is bounded across completed sessions,
                        # not only within each stream. Never evict an active turn.
                        active_sids={item['sid'] for item in self._progress_operations.values()}
                        for old in list(self._transient_progress):
                            if len(self._transient_progress)<128:break
                            if old not in active_sids and old!=sid:self._transient_progress.pop(old,None)
                        target=self._transient_progress.get(sid)
                        if target is None or target['scope']!=scope:
                            target={'sid':sid,'task_id':task_id,'scope':scope,'cursor':int(time.time()*1000000),'events':deque(maxlen=256),'timings':deque(maxlen=128)}
                            self._transient_progress[sid]=target
                        self._progress_operations[job_id]=target
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
                    'input':input_payload,'question_quality':self.question_quality_config()['config'],
                    'warm':ref_kind in ('answer','route'),'answer_trace':answer_run is not None,
                    'answer_result_token':self._answer_results[job_id].token if answer_run is not None else None}
                stop=asyncio.Event()
                from .stop_watch import watch_for_stop
                watcher=asyncio.create_task(watch_for_stop(store=self.store,task_id=task_id,session_id=sid,generation=operation.binding.generation,stop=stop,entry_version=claimed_answer[3] if claimed_answer is not None else None))
                def fence_stop(op):
                    task=self.store.task(task_id);session=self.store.session_binding(sid)
                    if task and task.get('stop_requested_at') is None and session and session['generation']==op.binding.generation:
                        raise PermissionError('stop signal has no durable stop/revocation evidence')
                if parked_entry is not None:
                    self._record_engine_timings([(sid,'engine.session','Reusing an existing session','started')])
                    def on_event(event):
                        if event.state=='running':
                            self._record_engine_timings([(sid,'engine.session','Reusing an existing session','completed')])
                else:
                    self._record_engine_timings([(sid,'engine.session','Warming up a fresh session','started')])
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
                                        'input':cfg['input'],'input_ref':cfg['input_ref'],'answer_result_token':cfg['answer_result_token'],'fds':{'broker':0,'compute':1}},
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
                try:
                    if answer_run is not None:
                        answer_run.finish()
                        try:await asyncio.wait_for(self._answer_trace_ready[job_id].wait(),1)
                        except asyncio.TimeoutError:pass  # Diagnostics missing is not a work deadline or a zero count.
                        from .answer_trace import tables
                        from .postgres_scope import request_stats
                        stats=request_stats.get();child_trace=self._answer_traces.get(job_id)
                        parent_trace=tables(stats.trace) if stats else None
                        self._last_answer_trace={'job_id':job_id,'task_id':task_id,'session_id':sid,
                            'duration_ms':round((time.monotonic()-trace_started)*1000),
                            'parent':parent_trace,'child':child_trace,'accounting':answer_run.summary(),
                            'total_syncs':parent_trace['syncs']+sum(v['syncs'] for v in child_trace['callers'].values()) if parent_trace and child_trace and child_trace['complete'] else None}
                except BaseException:
                    watcher.cancel()
                    try:await watcher
                    except asyncio.CancelledError:pass
                    if handle is not None:
                        await self.supervisor.reap(handle,operation,failed=True)
                    drop_entry()
                    if progress_ctx is not None:progress_ctx.__exit__(None,None,None)
                    raise
                timings['supervisor']=int((time.monotonic()-supervisor_at)*1000);completion_at=time.monotonic()
                watcher.cancel()
                try:await watcher
                except asyncio.CancelledError:pass
                if child_error is not None and handle is not None:
                    await self.supervisor.reap(handle,operation,failed=True,failure_error=child_error)
                    drop_entry();handle=None
                retain=self.store.retain_answer_review if answer_run is not None else self.store.retain_decision_review
                if answer_run is not None:stopped_by_owner=stop.is_set()
                else:
                    task_after=self.store.task(task_id)
                    stopped_by_owner=bool(task_after and task_after.get('stop_requested_at') is not None)
                if stopped_by_owner:
                    if self.supervisor.last_result is None or not self.supervisor.last_result.reaped:
                        raise RuntimeError('stopped child reaping unverified')
                    retain(task_id,'child-job:'+job_id,'child-completion-v1',
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
                    result_receiver=self._answer_results.get(job_id)
                    completion_snapshot=result_receiver.snapshot if answer_run is not None and result_receiver is not None else None
                    accepted=result_receiver.accepted_event if completion_snapshot is not None else None
                    if completion_snapshot is None:
                        with self._registry() as registry:
                            completed_session=registry.get(sid,serve=False)
                            completion_snapshot=completed_session.snapshot()
                            if answer_run is not None:
                                from vmodel_contracts.interactive import EventType
                                accepted=registry._event(completed_session,EventType.INPUT_ACCEPTED)
                    if answer_run is not None:self._answer_snapshots[sid]=(completion_snapshot,accepted)
                    process_status=completion_snapshot.process_status
                    if process_status==ProcessStatus.COMPLETED:
                        from .generator_documents import GeneratorDocuments
                        from .artifact_publication import publish_document_revision
                        documents=GeneratorDocuments(self.store,capability,job_id,
                            expected_attempt=self.store.worker_job(job_id)['attempt'])
                        publish_document_revision(self.store,task_id,documents,
                            writer_instance='interview-child:'+operation.operation_id)
                    timings['completion']=int((time.monotonic()-completion_at)*1000);timings['run']=int((time.monotonic()-run_at)*1000)
                    completion_record={'operation_id':operation.operation_id,'published_sequence':completion_snapshot.sequence,'compute_ms':str(compute.value),'reaped':True,'timings':timings,'provider_timings':broker.timings}
                    if answer_run is None:retain(task_id,'child-job:'+job_id,'child-completion-v1',completion_record)
                    quiescent=True
                    # Parking decision: a verified completed operation on a
                    # warm-capable child whose session waits for the next input
                    # keeps its claimed child alive.
                    parking=(handle is not None and handle.state=='completed'
                        and (parked_entry is not None or cfg['warm']) and process_status==ProcessStatus.WAITING_INPUT)
                    completion_event=('Local child parked after durable publication' if parking else 'Local child reaped after durable publication',
                        {'job_id':job_id,'compute_ms':str(compute.value),'pid':handle.process.pid if handle is not None else self.supervisor.last_result.pid})
                    if answer_run is None:self.store.add_event(task_id,'child.completed',completion_event[0],payload=completion_event[1])
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
                return (completion_record,completion_event) if answer_run is not None else None
            return await execute_job(self.store,job_id,handler,
                verify_completion_quiescence=lambda cap:quiescent,verify_stopped_quiescence=lambda cap:quiescent,
                reuse_answer_authority=self.answer_accounting is not None and ref_kind=='answer' and operation.binding.provider=='deepseek',
                claimed_answer=claimed_answer)
        with _slot:
            timings['slot_wait']=int((time.monotonic()-slot_at)*1000)
            try:self._run(run())
            except BaseException:
                if claimed_answer is not None:self._drop_warm(sid,recycle=True)
                try:self.store.pause_interrupted_worker(job_id)
                except Exception:
                    from .diagnostic_evidence import failure
                    failure('child.pause.failed')
                raise


class ChildInterviewHost:
    def __init__(self,store,base,*,gateway_factory,session_dsn,endpoint,paid_runtime=None,answer_accounting=None):
        if not session_dsn:raise ValueError('child interview requires explicit Postgres session storage')
        self._client=ChildInterviewClient(store,base,gateway_factory=gateway_factory,dsn=session_dsn,endpoint=endpoint,paid_runtime=paid_runtime,answer_accounting=answer_accounting)
    def client(self):return self._client
    def close(self):self._client.close()
