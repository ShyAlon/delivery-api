"""Run a generator phase inside the service, looking to the runner like the child process it replaces.

The runner turns an engine's output lines into task state (progress, typed decisions, errors, completion) and sends
the founder's answers back on the engine's input. A VirtualProcess carries exactly that, so the runner's state
handling is unchanged. The generator runs in a worker thread under the money gateway (SameHostProducerWorker): every
provider call is admitted and measured by the service itself, not by a separate process.

Limits, by design of running in one process:
  * One phase runs at a time (the slot). The generator reads a few process-wide settings; they are set for the run
    and restored afterwards, which is only safe with a single run.
  * A thread cannot be killed. Stop is cooperative; a run that ignores it is released to the runner (exit -9) while
    the thread finishes on its own, and the slot stays held until then.
  * A pending gate question lives in memory. After a restart the generator resumes from its checkpoint and asks again.
"""
import asyncio,json,os,queue,threading,traceback
from contextlib import contextmanager

_STAGES=('needs','srs','architecture','items','tests','validation','review','repair','expert_opinion')
_ENV_PREFIXES=('VMODEL_','SPEC_GENERATOR_')
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


@contextmanager
def process_settings(env):
    """Set the generator's process-wide settings for one run and restore them exactly afterwards."""
    wanted={k:v for k,v in (env or {}).items() if k.startswith(_ENV_PREFIXES)}
    snapshot={k:v for k,v in os.environ.items() if k.startswith(_ENV_PREFIXES)}
    try:
        os.environ.update(wanted)
        yield
    finally:
        # The generator sets some of these itself; whatever it added or changed goes back to what it was.
        for k in [k for k in os.environ if k.startswith(_ENV_PREFIXES) and k not in snapshot]:os.environ.pop(k,None)
        os.environ.update(snapshot)


def stage_models(env):
    """Every stage uses the one reviewed route the phase selected; only DeepSeek runs in the service."""
    from spec_generator.model_config import StageModel
    routes={v for k,v in (env or {}).items() if k.startswith('SPEC_GENERATOR_MODEL_') and v}
    if len(routes)!=1:raise ValueError('exactly one model route is required for an in-process phase')
    provider,sep,model=routes.pop().partition(':')
    if provider!='deepseek' or not sep or not model:raise ValueError('only deepseek runs inside the service')
    # Each stage keeps the output cap the generator ships for it (the tests stage needs 12288, not the 8192 default).
    from spec_generator import model_config
    caps=model_config._STAGE_DEFAULTS
    return {stage:(StageModel('deepseek',model,max_tokens=caps[stage].max_tokens) if stage in caps else StageModel('deepseek',model)) for stage in _STAGES}


class InProcessEngine:
    def __init__(self,artifacts_base,*,gateway_factory,wire_client_factory):
        self.artifacts_base=artifacts_base
        self.gateway_factory=gateway_factory
        self.wire_client_factory=wire_client_factory
        self._slot=None

    def _worker(self,store,proc):
        from .same_host_producer import SameHostProducerWorker
        from spec_generator.worker_runtime import JobRuntime
        from spec_generator.telemetry import UsageLog
        def runtime(capability,root):
            # Called in the worker thread once the job is claimed: tell the phase lifecycle to start, then wait
            # for the runner to hand over the command and settings it built.
            proc._loop.call_soon_threadsafe(proc.started.set)
            command,env=proc.wait_command()
            return JobRuntime(stage_models(env),str(root/'private-llm-calls.jsonl'),lambda text:None)
        return SameHostProducerWorker(store,self.artifacts_base,gateway_factory=self.gateway_factory,
            usage_log_factory=lambda capability,root:UsageLog(capability.session_id),
            runtime_factory=runtime,wire_client_factory=self.wire_client_factory)

    def _produce(self,proc):
        from spec_generator import cli
        from spec_generator.cli_io import RunIO
        from spec_generator.errors import UserFacingError
        def producer(capability,root):
            command,env=proc.wait_command()
            argv=command[command.index('spec_generator.cli')+1:]
            out=_Sink(proc);code=0
            try:
                with process_settings(env):
                    cli.main(argv,io=RunIO(stdout=out,stderr=out,decide=proc.decide))
            except EngineStopped:
                code=130
            except UserFacingError as exc:
                out.write('\n'+exc.render()+'\n');code=1
            except SystemExit as exc:
                code=exc.code if isinstance(exc.code,int) else (0 if exc.code is None else 1)
            except BaseException:
                out.write(traceback.format_exc());out.flush();proc.finish(1);raise
            finally:out.flush()
            proc.finish(code)
        return producer

    async def dispatch(self,store,orchestrator,task_id,phase,job_id,options):
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
