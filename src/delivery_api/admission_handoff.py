"""Private same-host job descriptor transfer. No service listener is installed."""
import array
import json
import socket
from .producer_admission import current_worker_gateway
from .admission_transport import worker_admission_channel
from .tenancy import current_scope
from contextlib import contextmanager


@contextmanager
def send_job_gateway(control, store, task_id):
    if control.family != socket.AF_UNIX or control.type != socket.SOCK_SEQPACKET:
        raise ValueError('private handoff requires a Unix sequenced-packet connection')
    # Validate against durable live job state and the authenticated owner scope.
    gateway = current_worker_gateway(store, task_id)
    job = store.worker_job(gateway.job_id)
    envelope = {'version': 1, 'owner_id': current_scope().owner_id,
                'job_id': gateway.job_id, 'session_id': job['session_id']}
    raw = json.dumps(envelope).encode()
    if len(raw) > 4096:
        raise ValueError('job binding is too large')
    with worker_admission_channel(gateway) as channel:
        fds = array.array('i', [channel.fileno()])
        sent = control.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)])
        if sent != len(raw):
            raise OSError('incomplete job descriptor transfer')
        yield envelope
