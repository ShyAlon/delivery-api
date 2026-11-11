"""Fixed private child executable. Configuration arrives on its inherited FD.

No CLI parser, credentials in env, service listener or provider network. The
parent independently resolves immutable refs and pins the launch binding.

A warm child (private config 'warm' + an interview answer/advance/route
operation) keeps its claimed session alive after the first operation and
serves follow-up operations on the same stream: each follow-up frame is one
sendmsg carrying the JSON operation config plus that operation's private
broker/compute descriptors (SCM_RIGHTS). STOP and EOF keep their exact
cold-path meanings; per-op money/quiescence machinery is constructed and
torn down per operation exactly as in the cold path.
"""
import array,json,logging,os,queue,socket,threading
from pathlib import Path
from requi_child_contract import decode_operation,JobBinding,require_binding,LifecycleEvent,encode_event,_unique_pairs,_invalid_constant

MAX_FRAME=65537
MAX_DESCRIPTORS=4


def recv_frame(channel):
    """One warm follow-up frame: JSON line plus SCM_RIGHTS descriptors.

    The parent sends exactly one sendmsg per frame, so the descriptors attach
    to the first byte of that frame; the JSON remainder may still need more
    reads (stream semantics). Returns (payload, fds); (None, []) on EOF;
    ('STOP', []) on the stop sentinel.
    """
    fds=array.array('i')
    try:
        data,ancillary,_flags,_addr=channel.recvmsg(MAX_FRAME,socket.CMSG_SPACE(MAX_DESCRIPTORS*4))
    except OSError:
        return None,[]
    for level,ctype,cdata in ancillary:
        if level==socket.SOL_SOCKET and ctype==socket.SCM_RIGHTS:
            fds.frombytes(cdata[:len(cdata)//4*4])
    while data and not data.endswith(b'\n'):
        chunk=channel.recv(MAX_FRAME)
        if not chunk:break
        data+=chunk
    if data==b'STOP\n':return 'STOP',[]
    if not data:return None,[]
    if len(data)>MAX_FRAME or not data.endswith(b'\n'):raise ValueError('bounded warm frame required')
    return json.loads(data,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant),list(fds)


def install_engine_timing_log():
    """The child configures no logging; route engine timing records to stderr as
    bare dotted-event lines. The parent retains only the structured timing shape
    from stderr (child_supervisor._forward_engine_timing); nothing else changes."""
    log=logging.getLogger('interview_engine')
    if any(getattr(handler,'_requi_timing',False) for handler in log.handlers):return
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    handler._requi_timing=True
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate=False


def main():
    install_engine_timing_log()
    channel=socket.socket(fileno=int(os.environ['REQUI_CHILD_FD']))
    stream=channel.makefile('rwb',buffering=0)
    config_socket=socket.socket(fileno=int(os.environ['REQUI_CHILD_CONFIG_FD']))
    config_stream=config_socket.makefile('rb')
    raw=config_stream.readline(32769)
    if not raw.endswith(b'\n') or len(raw)>32768:raise ValueError('bounded launch config required')
    cfg=json.loads(raw,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
    config_stream.close();config_socket.close()
    op=decode_operation(stream.readline(65537));binding=JobBinding.model_validate(cfg['binding']);require_binding(op,binding)
    # No arbitrary factory/import path can arrive through private configuration.
    if cfg.get('version')!=1 or cfg.get('provider')!=binding.provider or binding.provider not in ('ollama','deepseek'):
        raise PermissionError('pinned parent broker provider required')
    root=Path(cfg['owner_root'])
    if not root.is_absolute() or root.resolve()!=root:raise PermissionError('canonical owner root required')
    sequence=0;stop=threading.Event();gone=threading.Event();current=[op]
    warm=bool(cfg.get('warm')) and op.operation.kind in ('interview.answer','interview.advance','interview.route')
    frames=queue.Queue()
    def event(state):
        nonlocal sequence
        if gone.is_set():raise RuntimeError('parent descriptor closed')
        sequence+=1;stream.write(encode_event(LifecycleEvent(version=1,operation_id=current[0].operation_id,sequence=sequence,state=state)))
    def watch():
        try:line=stream.readline(6)
        except OSError:line=b''
        if line!=b'STOP\n':gone.set()
        stop.set()
    def warm_watch():
        # Sole stream reader in warm mode: follow-up operation frames queue
        # for the main thread; STOP and EOF keep their cold-path meanings.
        try:
            while not stop.is_set() and not gone.is_set():
                payload,fds=recv_frame(channel)
                if payload is None:gone.set();frames.put(None);return
                if payload=='STOP':stop.set();frames.put(None);return
                frames.put((payload,fds))
        except OSError:
            gone.set();frames.put(None)
    event('ready');threading.Thread(target=warm_watch if warm else watch,daemon=True,name='child-parent-watch').start()
    from requi_child_contract.broker import BrokerClient
    from requi_child_contract.compute import ComputeClient,measured_work
    from interview_engine.child_cursor import broker_call_id
    if op.operation.kind=='generator.phase':
        from spec_generator.child_execution import broker_call_id
    broker=BrokerClient(int(os.environ['REQUI_CHILD_BROKER_FD']),next_call_id=broker_call_id)
    compute=ComputeClient(int(os.environ['REQUI_CHILD_COMPUTE_FD'])) if 'REQUI_CHILD_COMPUTE_FD' in os.environ else None
    from contextlib import nullcontext
    from interview_engine.tenant_worker import _root
    root_token=_root.set(root)
    pool=None;progress_channel=None
    wire_trace=None
    if cfg.get('answer_trace'):
        from .answer_trace import WireTrace
        wire_trace=WireTrace();wire_trace.install()
    try:
        if stop.is_set():raise RuntimeError('parent stopped before dispatch')
        event('running')
        with measured_work(compute) if compute is not None and not warm else nullcontext():
            if op.operation.kind.startswith('interview.'):
                from interview_engine.pg_pool import build_pool
                from interview_engine.tenant_worker import pg_session_store
                from interview_engine.child_operations import execute_operation
                from vmodel_contracts.interactive import StartSessionRequest,InputResponse,ModelRouteSwitchRequest
                pool=build_pool(cfg['dsn'],max_size=4,name='child-operation')
                store=pg_session_store(pool,str(binding.owner_id),str(binding.auth_session_id))
                if 'REQUI_CHILD_PROGRESS_FD' in os.environ:
                    from .child_progress import install_progress_wakes
                    progress_channel=install_progress_wakes(store,int(os.environ['REQUI_CHILD_PROGRESS_FD']))
                from interview_engine import question_config
                question_config.store().put(cfg['question_quality'])
                from interview_engine.stop_fence import _check,StopRequested
                def check():
                    if stop.is_set():raise StopRequested('parent requested stop')
                def loaders(input_value,input_ref,kind):
                    return (
                        lambda ref:(ModelRouteSwitchRequest.model_validate(input_value) if kind=='interview.route' else StartSessionRequest.model_validate(input_value)) if ref==input_ref else None,
                        lambda ref:InputResponse.model_validate(input_value) if ref==input_ref else None)
                token=_check.set(check)
                warm_handle=None
                try:
                    if warm:
                        from interview_engine.child_operations import begin_warm,execute_warm
                        from interview_engine.wire_binding import broker_clients
                        req,ans=loaders(cfg['input'],cfg['input_ref'],op.operation.kind)
                        with measured_work(compute) if compute is not None else nullcontext():
                            warm_handle,_=begin_warm(op,trusted_binding=binding,store=store,broker=broker,
                                load_request=req,load_answer=ans)
                        if wire_trace is not None:wire_trace.emit(op.operation_id)
                        event('stopped' if stop.is_set() else 'completed')
                        # Follow-up operations on the held session: each frame
                        # carries its own private broker/compute descriptors,
                        # so per-op money and quiescence machinery is built
                        # and torn down exactly as in the cold path.
                        while not stop.is_set() and not gone.is_set():
                            item=frames.get()
                            if item is None:break
                            payload,fds=item
                            try:
                                op2=decode_operation(json.dumps(payload['operation'],separators=(',',':'),allow_nan=False).encode()+b'\n')
                                binding2=JobBinding.model_validate(payload['binding'])
                                fdmap={name:fds[index] for name,index in payload.get('fds',{}).items()}
                                current[0]=op2
                                broker2=BrokerClient(fdmap['broker'],next_call_id=broker_call_id)
                                compute2=ComputeClient(fdmap['compute']) if 'compute' in fdmap else None
                                if wire_trace is not None:wire_trace.reset()
                                event('running')
                                try:
                                    req2,ans2=loaders(payload.get('input'),payload.get('input_ref'),op2.operation.kind)
                                    with measured_work(compute2) if compute2 is not None else nullcontext():
                                        with broker_clients(broker2):
                                            execute_warm(warm_handle,op2,trusted_binding=binding2,
                                                load_request=req2,load_answer=ans2)
                                finally:
                                    broker2.close()
                                    if compute2 is not None:compute2.close()
                                if wire_trace is not None:wire_trace.emit(op2.operation_id)
                                event('stopped' if stop.is_set() else 'completed')
                            finally:
                                for fd in fds:
                                    try:os.close(fd)
                                    except OSError:pass
                    else:
                        req,ans=loaders(cfg['input'],cfg['input_ref'],op.operation.kind)
                        execute_operation(op,trusted_binding=binding,store=store,broker=broker,
                            load_request=req,load_answer=ans)
                finally:
                    _check.reset(token)
                    if warm_handle is not None:warm_handle.close()
            else:
                from interview_engine.pg_pool import build_pool
                from interview_engine.tenant_worker import pg_session_store
                from .child_generator_entry import execute
                pool=build_pool(cfg['dsn'],max_size=4,name='child-generator')
                store=pg_session_store(pool,str(binding.owner_id),str(binding.auth_session_id))
                execute(op,cfg,store,broker,stop,int(os.environ['REQUI_CHILD_OUTPUT_FD']))
        if gone.is_set() and not warm:raise RuntimeError('parent lost after dispatch')
        # A warm child emits per-operation terminal events inside its loop;
        # after the loop the parent reaps by STOP or descriptor close.
        if not warm:
            if wire_trace is not None:wire_trace.emit(op.operation_id)
            event('stopped' if stop.is_set() else 'completed')
    finally:
        if wire_trace is not None:
            wire_trace.emit(current[0].operation_id);wire_trace.restore()
        if progress_channel is not None:progress_channel.close()
        if pool is not None:pool.close()
        _root.reset(root_token);broker.close()
        if compute is not None:compute.close()
        channel.close()


if __name__=='__main__':
    try:main()
    except BaseException as error:
        from .child_failure import emit
        emit(error)
        raise SystemExit(1) from None
