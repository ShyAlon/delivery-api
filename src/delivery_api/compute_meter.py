"""Monotonic work intervals; excludes union of provider/founder wait intervals."""
from contextvars import ContextVar
from contextlib import contextmanager
from decimal import Decimal
import threading,time

_active=ContextVar('retail_compute_meter',default=None)

class ComputeMeter:
    def __init__(self,clock=time.monotonic_ns):
        self.clock=clock;self.lock=threading.Lock();self.depth=0
        self.started=clock();self.pause_started=None;self.excluded=0

    def pause(self):
        with self.lock:
            if self.depth==0:self.pause_started=self.clock()
            self.depth+=1

    def resume(self):
        with self.lock:
            if self.depth<=0:raise ValueError('unmatched compute pause')
            self.depth-=1
            if self.depth==0:
                self.excluded+=self.clock()-self.pause_started;self.pause_started=None

    def milliseconds(self):
        with self.lock:
            if self.depth:raise ValueError('live provider/founder wait; compute unsettled')
            elapsed=self.clock()-self.started-self.excluded
            return Decimal(max(0,elapsed))/Decimal(1000000)

@contextmanager
def bind(meter):
    token=_active.set(meter)
    try:yield meter
    finally:_active.reset(token)

@contextmanager
def excluded_wait():
    meter=_active.get()
    if meter:meter.pause()
    try:yield
    finally:
        if meter:meter.resume()

def current():return _active.get()
