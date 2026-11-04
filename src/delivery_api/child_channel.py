"""One bounded private JSON-line receiver lifecycle; schemas stay protocol-owned."""
from contextlib import contextmanager
import contextvars,json,socket,threading
from requi_child_contract import _unique_pairs,_invalid_constant
from .diagnostic_evidence import thread_target,failure


@contextmanager
def json_channel(handle,*,limit,name,quiescence_error,on_close=lambda:None):
    parent,child=socket.socketpair();scope=contextvars.copy_context()
    def serve():
        stream=parent.makefile('rwb')
        try:
            while True:
                raw=stream.readline(limit+1)
                if not raw:break
                if len(raw)>limit or not raw.endswith(b'\n'):raise ValueError('bounded private frame required')
                reply=handle(json.loads(raw,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant))
                stream.write(reply+b'\n');stream.flush()
        except BaseException:
            failure('child.'+name+'.failed')
        finally:
            try:parent.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            try:stream.close()
            except OSError:pass  # peer may be killed after the reply was buffered
            finally:parent.close()
    thread=threading.Thread(target=thread_target(lambda:scope.run(serve)),daemon=True,name='child-'+name);thread.start()
    try:yield child
    finally:
        child.close()
        try:parent.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        thread.join(timeout=2)
        if thread.is_alive():raise quiescence_error
        parent.close();on_close()
