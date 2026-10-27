"""Typed generator operation in its own process, no service or shell."""
import json,socket,threading
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4


def execute(operation,cfg,store,broker,stop,output_fd):
    from .child_generator import manifest
    from spec_generator.child_execution import DurableGate,GateSuspended
    from spec_generator.wire_binding import broker_clients
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.stop_fence import StopFence,execution_fence,StopRequested
    from spec_generator.telemetry import UsageLog
    from spec_generator.cli_io import RunIO
    from spec_generator import cli,model_config
    b=operation.binding;root=Path(cfg['owner_root'])/b.session_id
    value=cfg['input'];actual,digest=manifest(value['argv'],root)
    if actual!=value or digest!=operation.operation.input_manifest_sha256:raise PermissionError('generator input manifest differs')
    if '--to' in value['argv'] and value['argv'][value['argv'].index('--to')+1]!=operation.operation.phase:
        raise PermissionError('typed generator phase differs from reviewed CLI boundary')
    if (b.provider,b.model)!=('ollama',cfg.get('model',b.model)):raise PermissionError('local pinned generator model required')
    output=socket.socket(fileno=output_fd);stream=output.makefile('wb',buffering=0);lock=threading.Lock();halt=threading.Event();lost=threading.Event()
    def publish(value):
        raw=json.dumps(value,separators=(',',':')).encode()+b'\n'
        if len(raw)>65536:raise ValueError('bounded generator output required')
        with lock:stream.write(raw)
    class Sink:
        def write(self,text):
            # Bound chunks even when a renderer emits a whole document.
            for start in range(0,len(text),8000):publish({'kind':'log','text':text[start:start+8000]})
            return len(text)
        def flush(self):pass
        def isatty(self):return False
    def heartbeat():
        while not halt.wait(30):
            try:
                if not store.heartbeat(claim,150):lost.set();return
            except Exception:lost.set();return
    anchor=None
    if b.generation>1:
        from vmodel_contracts.execution_anchor import read_execution_anchor,ExecutionAnchor
        # Parent supplies immutable installed binding, child verifies bytes.
        anchor=ExecutionAnchor(**cfg['execution_anchor'])
        if read_execution_anchor(root,expected=anchor)['document_sha256']!=b.execution_anchor_sha256:
            raise PermissionError('generator installed anchor changed')
    fence=StopFence(root/'interview'/'stop-request.json',execution_anchor=anchor)
    def check():
        if stop.is_set() or lost.is_set():raise StopRequested('parent stopped or generator lease lost')
        fence.check(b.generation)
    models={stage:StageModel(b.provider,b.model,max_tokens=value.max_tokens) for stage,value in model_config._STAGE_DEFAULTS.items()}
    runtime=JobRuntime(models,str(root/'private-llm-calls.jsonl'),lambda text:None)
    runtime.bind_worker(owner_id=str(b.owner_id),task_id=b.task_id,job_id=b.job_id,generation=b.generation,session_root=root)
    claim=store.claim_generator_expected(b,'generator-child#'+str(uuid4()),150)
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        with ExitStack() as stack:
            stack.enter_context(job_runtime(runtime));stack.enter_context(broker_clients(broker))
            stack.enter_context(cli_execution(fence,UsageLog(b.session_id)));stack.enter_context(execution_fence(check))
            sink=Sink()
            try:cli.main(value['argv'],io=RunIO(stdout=sink,stderr=sink,decide=DurableGate(cfg.get('answers',{}),publish)))
            except GateSuspended:return
            except SystemExit as exc:
                if exc.code not in (None,0):raise RuntimeError('generator CLI did not succeed') from exc
            check()
    finally:
        halt.set();thread.join(timeout=2)
        try:
            if thread.is_alive() or not store.release(claim):raise PermissionError('generator lease release unverified')
        finally:stream.close();output.close()
