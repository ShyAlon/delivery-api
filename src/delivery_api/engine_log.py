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
import re
from datetime import datetime,timezone

from . import config,server_log_buffer

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
