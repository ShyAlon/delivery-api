"""Stop only the owning disposable PG when its test process loses the pipe.

A separate session survives test process-group interruption. EOF also handles
SIGKILL, where Python atexit handlers cannot run. Abnormal exit preserves data.
"""
import os
from pathlib import Path
import subprocess
import sys


def watch(read_fd, scratch, pid, ctl):
    with os.fdopen(read_fd, 'rb') as pipe:
        while pipe.read(1):
            pass
    directory = Path(scratch)
    if directory.parent != Path('/tmp') or not directory.name.startswith('delivery-api-pg-'):
        return
    try:
        if int((directory/'postmaster.pid').read_text().splitlines()[0]) != pid:
            return
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if b'-D' not in command or os.fsencode(scratch) not in command:
            return
    except (OSError, ValueError):
        return
    subprocess.run([ctl, '-D', scratch, 'stop', '-m', 'fast', '-w', '-t', '5'],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=8)


def protect(scratch):
    import pgserver
    read_fd, write_fd = os.pipe()
    directory = Path(scratch)
    pid = int((directory/'postmaster.pid').read_text().splitlines()[0])
    ctl = Path(pgserver.__file__).parent/'pginstall/bin/pg_ctl'
    try:
        guardian = subprocess.Popen([sys.executable, __file__, str(read_fd), scratch,
                                     str(pid), str(ctl)], pass_fds=(read_fd,),
                                    start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    def close():
        os.close(write_fd)
        guardian.wait(timeout=10)
    return close


if __name__ == '__main__':
    watch(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4])
