"""Private parent provider channel, opt-in constructor seam only."""
import contextvars
from contextlib import contextmanager
import json
import socket
import threading
from requi_child_contract import _unique_pairs,_invalid_constant


@contextmanager
def provider_broker_channel(broker):
    parent,child=socket.socketpair();scope=contextvars.copy_context();failures=[]
    def serve():
        stream=parent.makefile('rwb')
        try:
            while True:
                raw=stream.readline(2*1024*1024+1)
                if not raw:break
                if not raw.endswith(b'\n') or len(raw)>2*1024*1024:raise ValueError('bounded broker request required')
                value=json.loads(raw,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
                reply=broker.dispatch(value)
                stream.write(json.dumps(reply,separators=(',',':')).encode()+b'\n');stream.flush()
        except BaseException as exc:
            failures.append(type(exc).__name__)
            # Close instead of leaking private SQL/credential/exception text.
        finally:
            try:parent.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            stream.close();parent.close()
    thread=threading.Thread(target=lambda:scope.run(serve),daemon=True,name='child-provider-broker');thread.start()
    try:yield child
    finally:
        child.close()
        try:parent.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        thread.join(timeout=2)
        if thread.is_alive():raise RuntimeError('provider broker still in flight; not quiescent')
        parent.close()
