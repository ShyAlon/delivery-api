"""Private compute transcript receiver. Missing finish never becomes a charge."""
from .diagnostic_evidence import thread_target
import contextvars
from contextlib import contextmanager
import json
import socket
import threading
from requi_child_contract import _unique_pairs,_invalid_constant
from .child_compute_events import ChildComputeEvents,ComputeEvidenceError


class ChildComputeAccounting:
    def __init__(self,store,operation,*,rates,phase,journal):
        self.store=store;self.operation=operation;self.rates=rates;self.phase=phase;self.journal=journal
        self.events=ChildComputeEvents();self.transcript=[];self.segment=operation.binding.job_id+':child:'+operation.operation_id
        self.started=False;self.settled=False

    def begin(self):
        if self.started:raise ComputeEvidenceError('child segment already opened')
        self.store.begin_retail_compute(self.segment,self.operation.binding.job_id,self.phase,self.rates.snapshot())
        self.started=True

    def accept(self,event):
        if not self.started or self.settled:raise ComputeEvidenceError('child segment not open')
        if len(self.transcript)>=10000:raise ComputeEvidenceError('child compute transcript limit exceeded')
        self.events.accept(event);self.transcript.append(event)

    def finish(self):
        value=self.events.milliseconds()
        # Persist complete exact transcript before money. Existing immutable
        # parent staging journal; a failed ack cannot create another segment.
        key=(self.operation.operation_id,'compute','compute')
        import hashlib
        raw=json.dumps(self.transcript,separators=(',',':')).encode()
        digest=hashlib.sha256(raw).hexdigest()
        prior=self.journal.load(key)
        if prior is None:
            self.journal.begin(key,digest,self.operation.binding.provider,self.operation.binding.model)
            self.journal.complete(key,{'events':self.transcript,'milliseconds':str(value)},None)
        elif prior.get('request_sha256')!=digest or prior.get('response')!={'events':self.transcript,'milliseconds':str(value)}:
            raise ComputeEvidenceError('child compute durable transcript differs')
        self.store.finish_retail_compute(self.segment,value);self.settled=True
        return value

    def abort(self):
        if self.started and not self.settled:self.store.close_failed_retail_compute(self.segment)


@contextmanager
def child_compute_channel(accounting):
    parent,child=socket.socketpair();scope=contextvars.copy_context();failures=[]
    accounting.begin()
    def serve():
        stream=parent.makefile('rwb')
        try:
            while True:
                raw=stream.readline(4097)
                if not raw:break
                if not raw.endswith(b'\n') or len(raw)>4096:raise ComputeEvidenceError('bounded compute frame required')
                event=json.loads(raw,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
                accounting.accept(event)
                if event['kind']=='finish':accounting.finish()
                stream.write(str(event['sequence']).encode()+b'\n');stream.flush()
        except BaseException as exc:
            from .diagnostic_evidence import failure
            failure('child.compute.failed')
            failures.append(type(exc).__name__)
        finally:
            try:parent.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            stream.close();parent.close()
    thread=threading.Thread(target=thread_target(lambda:scope.run(serve)),daemon=True,name='child-compute-accounting');thread.start()
    try:yield child
    finally:
        child.close()
        try:parent.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        thread.join(timeout=2)
        if thread.is_alive():raise ComputeEvidenceError('child compute receiver not quiescent')
        if not accounting.settled:accounting.abort()
        parent.close()
