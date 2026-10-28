"""Local generator adapter: fresh child per run/gate, same publication owner.

The runner still consumes its existing line protocol. Only private fixed child
execution receives a validated CLI manifest; no shell or executable in payload.
"""
from .diagnostic_evidence import thread_target
import asyncio,hashlib,json,socket,sys,threading
from contextlib import contextmanager
from pathlib import Path
from .inprocess_engine import InProcessEngine,stage_models,EngineStopped
from .child_interview import LocalComputeEvidence,_slot
from .child_supervisor import ChildSupervisor


def manifest(argv,root):
    root=Path(root).resolve();files={}
    if not argv or not isinstance(argv,list) or any(not isinstance(v,str) for v in argv):
        raise PermissionError('typed generator argv required')
    # Match only the reviewed orchestrator command shape. Never allow another
    # CLI mode to add a path, export, replay directory or executable side effect.
    valued={'--conops','--system-baseline','--out','--to','--from'}
    flags={'--require-decisions','--review'}
    seen=set();index=1
    while index<len(argv):
        arg=argv[index]
        if arg in seen:raise PermissionError('duplicate generator option')
        seen.add(arg)
        if arg in valued:
            if index+1>=len(argv):raise PermissionError('generator option value missing')
            if arg in ('--to','--from') and argv[index+1] not in ('needs','srs','architecture','items','tests','validation'):
                raise PermissionError('reviewed generator stage required')
            index+=2
        elif arg in flags:index+=1
        else:raise PermissionError('unreviewed child generator option')
    # Only the CLI's explicit inputs, never output/checkpoint files.
    paths=[argv[0]]+[argv[i+1] for i,arg in enumerate(argv[:-1]) if arg in ('--conops','--system-baseline')]
    for path in paths:
        value=Path(path)
        if not value.is_absolute() or value.resolve()!=value or not value.is_relative_to(root) or not value.is_file():
            raise PermissionError('generator input must be a canonical owned file')
        files[str(value.relative_to(root))]=hashlib.sha256(value.read_bytes()).hexdigest()
    if '--out' not in argv or Path(argv[argv.index('--out')+1])!=root/'spec':raise PermissionError('owned generator output required')
    value={'argv':argv,'files':files}
    return value,hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@contextmanager
def output_channel(proc,gates):
    parent,child=socket.socketpair();errors=[]
    def read():
        stream=parent.makefile('rb')
        try:
            while True:
                raw=stream.readline(65537)
                if not raw:break
                if len(raw)>65536 or not raw.endswith(b'\n'):raise ValueError('bounded child output required')
                value=json.loads(raw)
                if set(value)=={'kind','text'} and value['kind']=='log' and isinstance(value['text'],str):proc.feed(value['text'].encode())
                elif set(value)=={'kind','key','decision'} and value['kind']=='gate':gates.append(value)
                else:raise ValueError('unknown child output')
        except BaseException as exc:
            from .diagnostic_evidence import failure
            failure('child.output.failed')
            errors.append(type(exc).__name__)
        finally:stream.close();parent.close()
    thread=threading.Thread(target=thread_target(read),daemon=True);thread.start()
    try:yield child
    finally:
        child.close();thread.join(timeout=2)
        if thread.is_alive():
            parent.shutdown(socket.SHUT_RDWR);thread.join(timeout=2)
        if thread.is_alive() or errors:raise RuntimeError('child output incomplete')


class ChildGeneratorEngine(InProcessEngine):
    def __init__(self,base,*,gateway_factory,dsn,endpoint,command=None):
        super().__init__(base,gateway_factory=gateway_factory,wire_client_factory=None)
        self.dsn=dsn;self.endpoint=endpoint;self.command=command or [sys.executable,'-m','delivery_api.child_entry']
        self._quiescent=True
    def _worker(self,store,proc):
        from .same_host_producer import SameHostProducerWorker
        from spec_generator.telemetry import UsageLog
        self.store=store
        def runtime(capability,root):
            # SameHost worker calls this in its worker thread, then installs no
            # provider wire or writer lock around the isolated child.
            proc._loop.call_soon_threadsafe(proc.started.set)
            command,env=proc.wait_command()
            routes={(m.provider,m.model) for m in stage_models(env).values()}
            if len(routes)!=1 or next(iter(routes))[0]!='ollama':raise PermissionError('paid generator child refused')
            from spec_generator.worker_runtime import JobRuntime
            return JobRuntime(stage_models(env),str(root/'private-llm-calls.jsonl'),lambda text:None)
        engine=self
        class Worker(SameHostProducerWorker):
            async def execute(self,job_id,producer):
                result=await super().execute(job_id,producer)
                if result and proc.returncode is None:proc.finish(0)
                return result
        return Worker(store,self.artifacts_base,gateway_factory=self.gateway_factory,
            usage_log_factory=lambda cap,root:UsageLog(cap.session_id),runtime_factory=runtime,
            isolated_quiescent=lambda:engine._quiescent)
    def _produce(self,proc):
        def produce(capability,root):
            from .child_operation_refs import ChildOperationRefs
            from .child_broker_journal import ChildBrokerJournal
            from .child_broker_authority import ChildBrokerAuthority
            from .child_provider_broker import ChildProviderBroker
            from .child_broker_channel import provider_broker_channel
            from .child_compute_channel import child_compute_channel
            from requi_child_contract import decode_operation
            from .db_pool import pool_for
            from interview_engine.tenant_worker import pg_session_store
            import httpx
            command,env=proc.wait_command();argv=command[command.index('spec_generator.cli')+1:]
            model_set={(m.provider,m.model) for m in stage_models(env).values()}
            if len(model_set)!=1 or next(iter(model_set))[0]!='ollama':raise PermissionError('local generator only')
            provider,model=next(iter(model_set));job_id=__import__('spec_generator.worker_runtime',fromlist=['current_runtime']).current_runtime().worker_binding[2]
            job=self.store.worker_job(job_id);phase=job['kind'].split(':')[-1]
            from .phases import PHASE_BY_KEY
            generator_stage=PHASE_BY_KEY[phase].generator_stage
            if not generator_stage:raise PermissionError('generator phase required')
            value,digest=manifest(argv,root);refs=ChildOperationRefs(self.store,capability.task_id)
            manifest_ref=refs.put('generator-manifest',value)
            self.store.retain_decision_review(capability.task_id,'child-generator:'+job_id,'manifest-v1',
                {'ref':manifest_ref,'sha256':digest,'provider':provider,'model':model})
            answers={}
            gateway=self.gateway_factory(capability,job_id)
            if getattr(gateway,'retail_rates',None) is not None:raise PermissionError('paid child remains refused')
            session_store=pg_session_store(pool_for(self.dsn),capability.scope.owner_id,capability.scope.session_id)
            while True:
                if proc.stop_requested:raise EngineStopped('generator stopped')
                row=self.store.session_binding(capability.session_id)
                if row['lease_epoch']==0:
                    claim=session_store.claim(capability.session_id,capability.scope.owner_id,'generator-bootstrap',30)
                    if claim is None or not session_store.release(claim):raise PermissionError('generator bootstrap lease unavailable')
                    row=self.store.session_binding(capability.session_id)
                binding={'owner_id':capability.scope.owner_id,'auth_session_id':capability.scope.session_id,'task_id':capability.task_id,
                    'session_id':capability.session_id,'job_id':job_id,'generation':capability.generation,
                    'lease_epoch':row['lease_epoch'],'provider':provider,'model':model}
                if capability.anchor_sha256:binding['execution_anchor_sha256']=capability.anchor_sha256
                body={'version':1,'operation_id':job_id,'binding':binding,'operation':{'kind':'generator.phase','phase':generator_stage,'input_manifest_sha256':digest}}
                op=decode_operation(json.dumps(body).encode()+b'\n')
                self.store.retain_decision_review(capability.task_id,'child-job:'+job_id,'child-launch:'+str(row['lease_epoch']),body)
                cfg={'version':1,'provider':'ollama','binding':binding,'dsn':self.dsn,'owner_root':str(root.parent),
                    'input_ref':manifest_ref,'input':refs.get(manifest_ref,'generator-manifest'),'answers':answers}
                if capability.execution_anchor is not None:cfg['execution_anchor']=capability.execution_anchor.document()
                gates=[]
                compute=LocalComputeEvidence(lambda value:self.store.retain_decision_review(capability.task_id,'child-job:'+job_id,'compute:'+str(row['lease_epoch']),value))
                supervisor=ChildSupervisor(self.command);self._quiescent=False
                async def launch():
                    with httpx.Client(trust_env=False,follow_redirects=False,timeout=3600) as http:
                        broker=ChildProviderBroker(op,gateway=gateway,journal=ChildBrokerJournal(self.store,op),client=http,
                            endpoint=self.endpoint,headers={},authorize=ChildBrokerAuthority(self.store,op,lease_epoch=row['lease_epoch']+1))
                        with provider_broker_channel(broker) as broker_fd,child_compute_channel(compute) as compute_fd,output_channel(proc,gates) as out_fd:
                            stop=asyncio.Event()
                            async def watch():
                                while not stop.is_set():
                                    task=self.store.task(capability.task_id)
                                    if proc.stop_requested or not task or task.get('stop_requested_at') is not None or not self.store.auth_session_active():
                                        stop.set();return
                                    await asyncio.sleep(.5)
                            watcher=asyncio.create_task(watch())
                            def persist_stop(operation):
                                task=self.store.task(capability.task_id)
                                if task and task.get('stop_requested_at') is None and self.store.auth_session_active():
                                    raise PermissionError('child Stop requires durable task stop or revocation')
                            try:
                                await supervisor.run(op,authorize=lambda op:capability.validate(self.store),private_config=cfg,
                                    descriptors={'broker':broker_fd.fileno(),'compute':compute_fd.fileno(),'output':out_fd.fileno()},
                                    stop=stop,persist_stop=persist_stop)
                            finally:
                                watcher.cancel()
                                try:await watcher
                                except asyncio.CancelledError:pass
                            if stop.is_set():raise EngineStopped('generator child stopped')
                    if not compute.settled:raise RuntimeError('generator compute evidence incomplete')
                with _slot:
                    asyncio.run(launch())
                    self._quiescent=bool(supervisor.last_result and supervisor.last_result.reaped)
                if not gates:return
                if len(gates)!=1:raise PermissionError('one durable gate per child step required')
                gate=gates[0];refs.put('generator-gate',gate)
                self.store.retain_decision_review(capability.task_id,'child-generator:'+job_id,'pending:'+gate['key'],gate)
                prior=self.store.decision_review(capability.task_id,'child-generator:'+job_id,'answer:'+gate['key'])
                answer=prior['answer'] if prior is not None else proc.decide(gate['decision'])
                self.store.retain_decision_review(capability.task_id,'child-generator:'+job_id,'answer:'+gate['key'],
                    {'job_id':job_id,'key':gate['key'],'answer':answer})
                answers[gate['key']]=answer
        return produce
