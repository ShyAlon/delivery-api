"""Fixed private child executable. Configuration arrives on its inherited FD.

No CLI parser, credentials in env, service listener or provider network. The
parent independently resolves immutable refs and pins the launch binding.
"""
import json,os,socket,threading
from pathlib import Path
from requi_child_contract import decode_operation,JobBinding,require_binding,LifecycleEvent,encode_event,_unique_pairs,_invalid_constant


def main():
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
    if cfg.get('version')!=1 or cfg.get('provider')!='ollama' or binding.provider!='ollama':
        raise PermissionError('only explicit local child cutover is installed')
    root=Path(cfg['owner_root'])
    if not root.is_absolute() or root.resolve()!=root:raise PermissionError('canonical owner root required')
    sequence=0;stop=threading.Event();gone=threading.Event()
    def event(state):
        nonlocal sequence
        if gone.is_set():raise RuntimeError('parent descriptor closed')
        sequence+=1;stream.write(encode_event(LifecycleEvent(version=1,operation_id=op.operation_id,sequence=sequence,state=state)))
    def watch():
        try:line=stream.readline(6)
        except OSError:line=b''
        if line!=b'STOP\n':gone.set()
        stop.set()
    event('ready');threading.Thread(target=watch,daemon=True,name='child-parent-watch').start()
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
    pool=None
    try:
        if stop.is_set():raise RuntimeError('parent stopped before dispatch')
        event('running')
        with measured_work(compute) if compute is not None else nullcontext():
            if op.operation.kind.startswith('interview.'):
                from interview_engine.pg_pool import build_pool
                from interview_engine.tenant_worker import pg_session_store
                from interview_engine.child_operations import execute_operation
                from vmodel_contracts.interactive import StartSessionRequest,InputResponse,ModelRouteSwitchRequest
                pool=build_pool(cfg['dsn'],max_size=4,name='child-operation')
                store=pg_session_store(pool,str(binding.owner_id),str(binding.auth_session_id))
                from interview_engine import question_config
                question_config.store().put(cfg['question_quality'])
                from interview_engine.stop_fence import _check,StopRequested
                def check():
                    if stop.is_set():raise StopRequested('parent requested stop')
                token=_check.set(check)
                try:
                    execute_operation(op,trusted_binding=binding,store=store,broker=broker,
                        load_request=lambda ref:(ModelRouteSwitchRequest.model_validate(cfg['input']) if op.operation.kind=='interview.route' else StartSessionRequest.model_validate(cfg['input'])) if ref==cfg['input_ref'] else None,
                        load_answer=lambda ref:InputResponse.model_validate(cfg['input']) if ref==cfg['input_ref'] else None)
                finally:_check.reset(token)
            else:
                from interview_engine.pg_pool import build_pool
                from interview_engine.tenant_worker import pg_session_store
                from .child_generator_entry import execute
                pool=build_pool(cfg['dsn'],max_size=4,name='child-generator')
                store=pg_session_store(pool,str(binding.owner_id),str(binding.auth_session_id))
                execute(op,cfg,store,broker,stop,int(os.environ['REQUI_CHILD_OUTPUT_FD']))
        if gone.is_set():raise RuntimeError('parent lost after dispatch')
        event('stopped' if stop.is_set() else 'completed')
    finally:
        if pool is not None:pool.close()
        _root.reset(root_token);broker.close()
        if compute is not None:compute.close()
        channel.close()


if __name__=='__main__':main()
