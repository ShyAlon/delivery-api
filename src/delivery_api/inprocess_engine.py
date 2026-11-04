"""Runner protocol bridge and serialized lifecycle for isolated child phases."""
import asyncio,json,os,queue,threading

_STAGES=('needs','srs','architecture','items','tests','validation','review','repair','expert_opinion')
_ATTACH_TIMEOUT_S=120.0
_registry={}


class EngineStopped(Exception):
    """The run was stopped or released before the generator finished."""


class _Stdin:
    def __init__(self,proc):self._proc=proc
    def write(self,data):
        text=data.decode() if isinstance(data,(bytes,bytearray)) else str(data)
        for line in text.splitlines():self._proc._answers.put(line)
    async def drain(self):return None


class _Sink:
    """Text stream the generator writes to; every complete line reaches the runner."""
    def __init__(self,proc):self._proc=proc;self._buffer=''
    def write(self,text):
        self._buffer+=text
        while '\n' in self._buffer:
            line,self._buffer=self._buffer.split('\n',1)
            self._proc.feed((line+'\n').encode())
        return len(text)
    def flush(self):
        if self._buffer:
            self._proc.feed(self._buffer.encode());self._buffer=''
    def isatty(self):return False


class VirtualProcess:
    """The slice of an asyncio subprocess the runner uses. It never has a pid and is never signalled."""
    virtual=True
    pid=None

    def __init__(self,loop):
        self._loop=loop
        self.stdout=asyncio.StreamReader(limit=2**20)
        self.stdin=_Stdin(self)
        self.returncode=None
        self.started=asyncio.Event()      # set by the producer thread once it holds the job
        self._done=asyncio.Event()
        self._command=None
        self._attached=threading.Event()
        self._answers=queue.Queue()
        self._stop=threading.Event()

    # runner side (event loop)
    def attach(self,command,env):
        self._command=(list(command),dict(env or {}));self._attached.set()
    async def wait(self):
        await self._done.wait();return self.returncode
    def terminate(self):self._request_stop()
    def kill(self):
        # The thread cannot be killed: release the runner now, the thread ends on its own.
        self._request_stop();self.finish(-9)
    def _request_stop(self):
        self._stop.set();self._answers.put(None)

    # producer side (worker thread)
    def feed(self,data):
        self._loop.call_soon_threadsafe(self._feed,data)
    def _feed(self,data):
        if self.returncode is None:self.stdout.feed_data(data)
    def finish(self,code):
        self._loop.call_soon_threadsafe(self._finish,code)
    def _finish(self,code):
        if self.returncode is not None:return
        self.returncode=code;self.stdout.feed_eof();self._done.set()
    def wait_command(self):
        if not self._attached.wait(_ATTACH_TIMEOUT_S) or self._stop.is_set():
            raise EngineStopped('the runner never attached to the in-process engine')
        return self._command
    @property
    def stop_requested(self):return self._stop.is_set()
    def decide(self,decision):
        """The generator asks the founder; the runner records the question and sends the answer on stdin."""
        self.feed(('REQUIRE_EVENT '+json.dumps(decision,separators=(',',':'))+'\n').encode())
        while True:
            answer=self._answers.get()
            if answer is None or self._stop.is_set():raise EngineStopped('stopped while waiting for the founder')
            return answer


def terminate(proc,sig):
    """Stop a run. A real child gets its process group signalled; a virtual one only gets the stop request."""
    import signal
    if getattr(proc,'virtual',False):
        proc.kill() if sig==signal.SIGKILL else proc.terminate();return
    if os.name=="posix":os.killpg(proc.pid,sig)
    else:proc.kill() if sig==signal.SIGKILL else proc.terminate()


def stage_models(env):
    """Every stage uses the one reviewed route the phase selected; managed routes only.

    Both supported providers use the same reviewed route checks everywhere.
    """
    from spec_generator.model_config import StageModel
    routes={v for k,v in (env or {}).items() if k.startswith('SPEC_GENERATOR_MODEL_') and v}
    if len(routes)!=1:raise ValueError('exactly one model route is required for an in-process phase')
    provider,sep,model=routes.pop().partition(':')
    from .local_route import managed_routes
    if not sep or not model or provider not in managed_routes():raise ValueError('the phase route is not a managed route on this service')
    # Each stage keeps the output cap the generator ships for it (the tests stage needs 12288, not the 8192 default).
    from spec_generator import model_config
    caps=model_config._STAGE_DEFAULTS
    return {stage:(StageModel(provider,model,max_tokens=caps[stage].max_tokens) if stage in caps else StageModel(provider,model)) for stage in _STAGES}


class ChildPhaseLifecycle:
    def __init__(self,artifacts_base,*,gateway_factory):
        self.artifacts_base=artifacts_base
        self.gateway_factory=gateway_factory
        self._slot=None

    async def dispatch(self,store,orchestrator,task_id,phase,job_id,options):
        claimed=await self._dispatch_one(store,orchestrator,task_id,phase,job_id,options)
        if not claimed:return False
        # The previous worker is settled and its serialized slot released before
        # reserving another exact job. Each provider call still owns admission.
        while store is not None:
            continuation=store.prepare_open_gate_continuation(task_id,phase,job_id)
            if continuation is None:break
            phase,job_id,options=(continuation[k] for k in ('phase','job_id','options'))
            from .orchestrator import ExecutionMode
            options={**options,'mode':ExecutionMode(options['mode'])}
            if not await self._dispatch_one(store,orchestrator,task_id,phase,job_id,options):
                raise ValueError('open-gate continuation job was already claimed')
        return True

    async def _dispatch_one(self,store,orchestrator,task_id,phase,job_id,options):
        """Run one phase job: the phase lifecycle on the event loop, the generator in a worker thread.

        Returns False when the job was already claimed; nothing ran."""
        if self._slot is None:self._slot=asyncio.Lock()
        async with self._slot:
            proc=VirtualProcess(asyncio.get_running_loop())
            _registry[task_id]=proc
            async def lifecycle():
                await proc.started.wait()
                return await orchestrator.execute_phase(task_id,phase,**options)
            phase_task=asyncio.ensure_future(lifecycle())
            failure=None;claimed=False
            try:
                from .diagnostic_evidence import bound
                with bound(task_id=task_id,job_id=job_id):
                    claimed=await self._worker(store,proc).execute(job_id,self._produce(proc))
            except BaseException as exc:
                failure=exc;proc.kill()
            try:
                if proc.started.is_set():
                    try:await phase_task
                    except BaseException:
                        if failure is None:raise
                else:phase_task.cancel()
            finally:
                _registry.pop(task_id,None)
                if proc.returncode is None:proc.finish(-9)
            if failure is not None:raise failure
            return claimed


def take(task_id):
    """The runner's hook: the virtual process the service prepared for this task, once."""
    return _registry.get(task_id)


_engine=None

def configure(engine):
    global _engine
    _engine=engine

def current():
    return _engine
