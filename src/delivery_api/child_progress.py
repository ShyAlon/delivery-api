"""Private, coalescing wake-only channel for committed child journal writes.

No progress payload crosses this channel. A wake only asks the parent to perform
its normal owner-scoped read. Missing/lost wakes have a bounded safety poll.
"""
from contextlib import contextmanager
import socket
import threading


@contextmanager
def progress_wake_channel(session_id):
    from . import event_bus
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    parent.settimeout(0.2)
    halt=threading.Event()
    def receive():
        while not halt.is_set():
            try:frame=parent.recv(2)
            except socket.timeout:continue
            except OSError:return
            if frame==b'.':event_bus.notify('engine-progress:'+session_id)
    thread=threading.Thread(target=receive,daemon=True,name='child-progress-wake')
    thread.start()
    try:yield child
    finally:
        halt.set();thread.join(timeout=1)
        parent.close();child.close()


def install_progress_wakes(store,fd):
    """Wrap this child's explicit store instance, after-commit methods only.
    Nonblocking datagrams coalesce under load; wake loss never fails a commit.
    Keep the socket alive for the lifetime of these bound wrappers.
    """
    channel=socket.socket(fileno=fd)
    channel.setblocking(False)
    for name in ('publish_step','append_observational_progress'):
        original=getattr(store,name)
        def committed(*args,_original=original,_name=name,**kwargs):
            result=_original(*args,**kwargs)
            changed=(_name=='append_observational_progress' or kwargs.get('journal_pages') or kwargs.get('journal_page'))
            try:
                if changed:channel.send(b'.')
            except OSError:pass  # advisory only; parent safety poll is authoritative
            return result
        setattr(store,name,committed)
    return channel
