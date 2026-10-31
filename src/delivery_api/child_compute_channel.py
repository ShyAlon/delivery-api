"""Private compute transcript receiver. Missing finish never becomes a charge."""
from contextlib import contextmanager
import json
from .child_channel import json_channel
from .child_compute_events import ChildComputeEvents,ComputeEvidenceError


class ChildComputeAccounting:
    def __init__(self,store,operation,*,rates,phase,journal):
        self.store=store;self.operation=operation;self.rates=rates;self.phase=phase;self.journal=journal
        self.events=ChildComputeEvents();self.transcript=[]
        invocation=operation.operation_id+':lease:'+str(operation.binding.lease_epoch)
        self.segment=operation.binding.job_id+':child:'+invocation
        self.evidence_key=(operation.operation_id,'compute:'+str(operation.binding.lease_epoch),'compute')
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
        key=self.evidence_key
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
    accounting.begin()
    def accept(event):
        accounting.accept(event)
        if event['kind']=='finish':accounting.finish()
        return str(event['sequence']).encode()
    with json_channel(accept,limit=4096,name='compute',quiescence_error=ComputeEvidenceError('child compute receiver not quiescent'),
                      on_close=lambda:None if accounting.settled else accounting.abort()) as child:yield child
