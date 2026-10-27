"""Private sequenced monotonic compute events. No local price or settlement."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import socket
import threading
import time

_current=ContextVar('child_compute_events',default=None)


class ComputeClient:
    def __init__(self,fd,clock=time.monotonic_ns):
        self.socket=socket.socket(fileno=os.dup(fd));self.socket.settimeout(10)
        self.stream=self.socket.makefile('rwb');self.lock=threading.Lock()
        self.clock=clock;self.sequence=0;self.failed=False;self.closed=False

    def emit(self,kind,wait_id=None):
        with self.lock:
            if self.failed or self.closed:raise RuntimeError('child compute channel closed')
            self.sequence+=1
            value={'sequence':self.sequence,'monotonic_ns':self.clock(),'kind':kind}
            if wait_id is not None:value['wait_id']=wait_id
            try:
                self.stream.write(json.dumps(value,separators=(',',':')).encode()+b'\n');self.stream.flush()
                ack=self.stream.readline(100)
                if ack!=str(self.sequence).encode()+b'\n':raise ValueError('compute sequence ack differs')
                if kind=='finish':self.closed=True
            except BaseException:
                self.failed=True;raise

    def close(self):self.stream.close();self.socket.close()


@contextmanager
def measured_work(client):
    token=_current.set(client)
    try:
        client.emit('start')
        yield
        client.emit('finish')
    finally:_current.reset(token)


@contextmanager
def excluded_wait(wait_id):
    client=_current.get()
    if client is not None:client.emit('wait.begin',wait_id)
    try:yield
    finally:
        if client is not None:client.emit('wait.end',wait_id)
