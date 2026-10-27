"""The engine process half of the Debug server log.

The interview engine runs as a separate process, so its output never reached the
in-memory server_log_buffer. The full-service launcher (scripts/docker/full_entry.py)
copies the engine's combined stdout/stderr into <data dir>/logs/engine.log, prefixing
each line with a UTC timestamp and rotating the file at a fixed size. This module reads
a bounded tail back, scrubs every line with server_log_buffer.scrub, and applies the
buffer's audience rule: a customer sees a line only when its session=<uuid> resolves to
their account through the delivery store; lines without a resolvable owner are
operator-only. Everything here is read-only and never fatal: a missing or unreadable
file is an empty, unavailable result, not an error."""
import logging
import os
import re
import threading
from datetime import datetime,timezone

from . import config,server_log_buffer
from .log_durability import degraded

TAIL_BYTES=256*1024
MAX_LINES=400
_STAMP=re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s')
_SESSION=re.compile(r'\bsession=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b')
HEADER='--- engine process (bounded scrubbed tail of engine.log) ---'


def default_path():
    """The same file full_entry.engine_log_path writes (config.default_data_dir()/logs/engine.log)."""
    return config.default_data_dir()/'logs'/'engine.log'


def _tail(path):
    try:
        with open(path,'rb') as handle:
            handle.seek(0,2);size=handle.tell()
            handle.seek(max(0,size-TAIL_BYTES))
            data=handle.read()
    except OSError:return None
    return data.decode('utf-8',errors='replace').splitlines()[-MAX_LINES:]


def read(owner_id,admin,minutes,resolve_owner,path=None,now=None):
    """Newest scrubbed engine lines in the window, formatted like server_log_buffer rows.

    resolve_owner maps a session id to an owner id (or None); it is called at most once
    per distinct session id in the tail and its failures mean operator-only. Stamped
    lines outside the minutes window are dropped; a rare unstamped line (foreign or a
    rotation boundary) stays, operator-only, since the bounded tail is recent by shape."""
    import time
    now=time.time() if now is None else now
    minutes=max(1,min(int(minutes),240));since=now-minutes*60
    path=path or default_path()
    lines=_tail(path)
    if lines is None:return {'available':False,'text':'','count':0,'path':str(path)}
    owners={}
    out=[]
    for line in lines:
        text=server_log_buffer.scrub(line)
        stamp=None
        m=_STAMP.match(text)
        if m:
            try:stamp=datetime.strptime(m.group(1),'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
            except ValueError:stamp=None
        if stamp is not None and stamp<since:continue
        owner=None
        found=_SESSION.search(text)
        if found:
            session=found.group(1)
            if session not in owners:
                try:owners[session]=resolve_owner(session)
                except Exception:owners[session]=None
            owner=owners[session]
        if not admin and (owner is None or owner!=owner_id):continue
        when=datetime.fromtimestamp(stamp if stamp is not None else now,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        who=('' if not admin else ' [you]' if owner==owner_id else ' [other:%s]'%owner[:8] if owner else ' [system]')
        out.append('%s engine%s: %s'%(when,who,text))
    if not out:return {'available':True,'text':'','count':0,'path':str(path)}
    return {'available':True,'text':HEADER+'\n'+'\n'.join(out),'count':len(out),'path':str(path)}


MIRROR_MAX_BYTES=2*1024*1024   # same bound as scripts/docker/full_entry.py's tee


class _MirrorHandler(logging.Handler):
    """Append interview_engine log records to the same engine.log the docker tee writes.

    The Mac full service hosts the engine in-process (cloud_runtime), so the real engine
    stream - swallowed stops, fence trips, lease heartbeats - never reaches the tee'd
    subprocess file: the uvicorn engine on 8010 sits idle and engine.log shows only its
    startup lines. With this handler the in-process engine's records land in the same
    bounded file the Debug engine tail reads. Lines are scrubbed on write, stamped like
    the tee's, and the file rotates once at MIRROR_MAX_BYTES. Never fatal: the first
    write error disables the handler."""

    def __init__(self,path,level):
        super().__init__(level)
        self._path=path
        self._dead=False
        self._lock=threading.Lock()

    def emit(self,record):
        if self._dead:return
        try:
            line=server_log_buffer.scrub(record.getMessage())
            rows=[line]
            if record.exc_info:
                fmt=self.formatter or logging.Formatter()
                rows+=['    '+scrubbed for scrubbed in
                       (server_log_buffer.scrub(l) for l in fmt.formatException(record.exc_info).splitlines()[-40:])]
            stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            text=''.join('%s %-7s %s: %s\n'%(stamp,record.levelname,record.name,row) for row in rows)
            with self._lock:
                self._path.parent.mkdir(parents=True,exist_ok=True)
                if self._path.exists() and self._path.stat().st_size>MIRROR_MAX_BYTES:
                    os.replace(self._path,self._path.with_suffix('.log.1'))
                with open(self._path,'a',encoding='utf-8') as handle:handle.write(text)
        except Exception as error:
            self._dead=True
            degraded('engine-mirror',error)


_mirrors=[]


def install_mirror(path=None,level=None,logger_name='interview_engine'):
    """Mirror the in-process engine's records into engine.log. Safe to call more than once.

    INFO level: heartbeat lifecycle lines (start/lost/give-up) are INFO and WARNING and
    belong in the operator's file; the customer-visible in-memory buffer stays WARNING-only."""
    if _mirrors:return _mirrors[0][1]
    handler=_MirrorHandler(path or default_path(),logging.INFO if level is None else level)
    logger=logging.getLogger(logger_name)
    if logger.getEffectiveLevel()>handler.level:logger.setLevel(handler.level)   # engine configures no logging; root is WARNING
    logger.addHandler(handler)
    _mirrors.append((logger_name,handler))
    return handler


def uninstall_mirror():
    for name,handler in _mirrors:
        logging.getLogger(name).removeHandler(handler)
    _mirrors.clear()
