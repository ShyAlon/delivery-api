"""Job-bound local socketpair protocol. No listener, URL, token or public API.

One connection belongs to one trusted child process. A production runner must
pass only this descriptor to that child; wiring is still pending. Bounded JSON,
not pickle, crosses the boundary. No reconnect/retry of ambiguous starts.
"""
import contextvars,json,socket,threading
from contextlib import contextmanager

MAX_FRAME=1024*1024

@contextmanager
def worker_admission_channel(gateway):
    parent,child=socket.socketpair()
    scope=contextvars.copy_context()
    def serve():
        stream=parent.makefile('rwb')
        try:
            while True:
                raw=stream.readline(MAX_FRAME+1)
                if not raw:break
                if len(raw)>MAX_FRAME or not raw.endswith(b'\n'):break
                try:
                    request=json.loads(raw)
                    if not isinstance(request,dict) or set(request)!={'action','payload'}:raise ValueError()
                    action=request['action'];payload=request['payload']
                    if action=='reserve':
                        if set(payload)!={'attempt_id','stage','provider','model','messages','max_output_tokens'}:raise ValueError()
                        result=gateway.reserve(**payload)
                    elif action=='reserve_serialized':
                        if set(payload)!={'attempt_id','stage','provider','model','serialized_request','max_output_tokens'}:raise ValueError()
                        result=gateway.reserve_serialized(**payload)
                    elif action=='settle_raw':
                        if set(payload)!={'attempt','raw_usage'}:raise ValueError()
                        result=gateway.settle_raw(payload['attempt'],payload['raw_usage'])
                    elif action=='release':
                        if set(payload)!={'attempt','evidence'}:raise ValueError()
                        result=gateway.release(payload['attempt'],payload['evidence'])
                    elif action in ('start','uncertain'):
                        if set(payload)!={'attempt'}:raise ValueError()
                        result=getattr(gateway,action)(payload['attempt'])
                    elif action=='settle':
                        if set(payload)!={'attempt','usage'}:raise ValueError()
                        # Only normalized usage, never a producer supplied price.
                        from types import SimpleNamespace
                        result=gateway.settle(payload['attempt'],SimpleNamespace(usage_metadata=payload['usage']))
                    else:raise ValueError()
                    reply={'ok':True,'result':result}
                except Exception as exc:
                    # The worker used to get only "denied", so a refusal looked like a dropped connection.
                    # Our own refusal text is safe to show; any other exception (database, I/O) is named
                    # by class only so no SQL, path or credential text crosses the boundary.
                    name=type(exc).__name__
                    reason=name+': '+str(exc)[:200] if name in ('AdmissionDenied','TenantAccessDenied') and str(exc) else name
                    reply={'ok':False,'error':'worker attempt admission denied','reason':reason}
                stream.write(json.dumps(reply).encode()+b'\n');stream.flush()
        finally:stream.close();parent.close()
    thread=threading.Thread(target=lambda:scope.run(serve),daemon=True);thread.start()
    try:yield child
    finally:
        child.close()
        try:parent.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        parent.close();thread.join(timeout=2)
