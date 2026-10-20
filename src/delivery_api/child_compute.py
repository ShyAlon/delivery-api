"""Compute charge for a phase that runs as a child process: CPU seconds of the child and its process group.

Thread mode charges elapsed time minus provider and founder waits (same_host_producer). A child process is measured by the CPU it
really used, read from /proc by the run_slots watchdog. A run that mostly waits on model calls therefore costs almost no compute
credit; tokens carry that cost. The price per 10 ms is unchanged."""
import uuid
from decimal import Decimal

CPU_MS_PER_SECOND=Decimal(1000)


def cpu_ms(cpu_seconds):
    """CPU seconds as the milliseconds the ledger prices. None (never read) counts as zero."""
    if cpu_seconds is None:return Decimal(0)
    return (Decimal(str(cpu_seconds))*CPU_MS_PER_SECOND).quantize(Decimal('0.001'))


def available(store):
    return callable(getattr(store,'begin_retail_compute',None)) and callable(getattr(store,'finish_retail_compute',None))


def begin(store,job_id,phase):
    """Open one compute segment for this child run. Each run has its own key, so an explicit resume of a job starts a fresh segment."""
    from .pricing import retail_rates
    key='%s:cpu:%s'%(job_id,uuid.uuid4().hex[:8])
    store.begin_retail_compute(key,job_id,phase,retail_rates().snapshot())
    return key


def settle(store,key,cpu_seconds):
    """Post the CPU charge and close the segment. Returns the milliseconds charged."""
    ms=cpu_ms(cpu_seconds)
    store.finish_retail_compute(key,ms)
    return ms
