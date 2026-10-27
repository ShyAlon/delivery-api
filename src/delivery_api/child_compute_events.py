"""Exact child monotonic work minus union of named waits; no crash estimate.

Inert accumulator. Durable admission/segment integration must require the full
sequenced transcript. Any inconsistency poisons it permanently. IPC delivery
latency is never substituted for child timestamps. Paid refusal stays intact.
"""
from decimal import Decimal


class ComputeEvidenceError(ValueError):pass


class ChildComputeEvents:
    def __init__(self):
        self.sequence=0;self.last=None;self.started=None;self.finished=None
        self.waits=set();self.pause=None;self.excluded=0;self.failed=False

    def accept(self,event):
        try:
            if self.failed or self.finished is not None:raise ValueError('closed compute evidence')
            if not isinstance(event,dict) or set(event)-{'sequence','monotonic_ns','kind','wait_id'}:raise ValueError('exact compute fields required')
            seq=event['sequence'];now=event['monotonic_ns'];kind=event['kind']
            if type(seq) is not int or seq!=self.sequence+1 or type(now) is not int or now<0 or (self.last is not None and now<self.last):
                raise ValueError('compute gap or non-monotonic clock')
            if kind=='start':
                if self.started is not None or seq!=1 or 'wait_id' in event:raise ValueError('duplicate compute start')
                self.started=now
            elif self.started is None:raise ValueError('compute start missing')
            elif kind=='finish':
                if self.waits or 'wait_id' in event:raise ValueError('compute finish with live waits')
                self.finished=now
            elif kind in ('wait.begin','wait.end'):
                wait=event.get('wait_id')
                if not isinstance(wait,str) or not 0<len(wait)<=200:raise ValueError('bounded wait identity required')
                if kind=='wait.begin':
                    if wait in self.waits:raise ValueError('duplicate wait')
                    if not self.waits:self.pause=now
                    self.waits.add(wait)
                else:
                    if wait not in self.waits:raise ValueError('unmatched wait end')
                    self.waits.remove(wait)
                    if not self.waits:self.excluded+=now-self.pause;self.pause=None
            else:raise ValueError('unknown compute event')
            self.sequence=seq;self.last=now
        except (ValueError,KeyError,TypeError) as exc:
            self.failed=True
            raise ComputeEvidenceError('incomplete or conflicting child compute evidence') from exc

    def milliseconds(self):
        if self.failed or self.finished is None or self.waits:raise ComputeEvidenceError('child compute unresolved; no guessed charge')
        return Decimal(self.finished-self.started-self.excluded)/Decimal(1000000)
