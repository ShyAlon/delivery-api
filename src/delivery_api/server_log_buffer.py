"""Bounded in-memory server log for the Debug dialog.

The service container is read-only, so there is no log file the app can read back. Warnings, errors and the activity log are kept here
(newest 2000 lines), scrubbed when they are written and again when they are read. A signed-in customer reads only lines made while
serving their own requests; the operator also reads system lines. Lines without a known owner are never shown to a customer."""
import collections
import logging
import re
import threading
import time
from datetime import datetime,timezone

from .log_safety import safe_text

MAX_LINES=2000
MAX_LINE=600
MAX_TRACE_LINES=40
_EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
_JWT=re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}')
_KEY=re.compile(r'\b(?:sk|pk|rk|sb)[-_][A-Za-z0-9_-]{12,}')
_DSN=re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s\'"]+',re.I)
_HEX=re.compile(r'\b[A-Fa-f0-9]{32,}\b')
_TOKENISH=re.compile(r'(?<![A-Za-z0-9_/.-])[A-Za-z0-9_-]{40,}={0,2}(?![A-Za-z0-9_/.-])')


def scrub(text):
    """Remove secrets, tokens, credentials and email addresses from one line of log text. Paths and ordinary words are kept."""
    value=safe_text(text,MAX_LINE)
    value=_DSN.sub(lambda m:m.group(0).split('://')[0]+'://[redacted]' if '@' in m.group(0) or m.group(0).lower().startswith(('postgres','mysql','redis','amqp')) else m.group(0),value)
    value=_JWT.sub('[jwt]',value)
    value=_KEY.sub('[key]',value)
    value=_EMAIL.sub('[email]',value)
    value=_HEX.sub('[hex]',value)
    value=_TOKENISH.sub(lambda m:'[token]' if re.search(r'\d',m.group(0)) and re.search(r'[A-Za-z]',m.group(0)) else m.group(0),value)
    return value


class _Buffer(logging.Handler):
    def __init__(self,lines,lock,level,root=False):
        super().__init__(level)
        self._lines,self._lock,self._root=lines,lock,root

    def emit(self,record):
        try:
            if record.name.startswith('uvicorn') and self._root:return
            owner=None
            try:
                from .tenancy import current_scope
                owner=current_scope().owner_id
            except Exception:owner=None
            text=record.getMessage()
            rows=[scrub(text)]
            if record.exc_info:
                rows+=[scrub(l) for l in self.formatter.formatException(record.exc_info).splitlines()[-MAX_TRACE_LINES:]] if self.formatter else []
            stamp=record.created
            with self._lock:
                for row in rows:
                    self._lines.append((stamp,owner,record.levelname,record.name,row))
        except Exception:
            pass


_lines=collections.deque(maxlen=MAX_LINES)
_lock=threading.Lock()
_handlers=[]


def install():
    """Attach to the activity log, uvicorn and the root logger. Safe to call more than once."""
    if _handlers:return
    plan=(('delivery_api.activity',logging.INFO),('uvicorn',logging.WARNING),('',logging.WARNING))
    for name,level in plan:
        handler=_Buffer(_lines,_lock,level,root=(name==''));handler.setFormatter(logging.Formatter())
        logging.getLogger(name).addHandler(handler);_handlers.append((name,handler))


def uninstall():
    for name,handler in _handlers:logging.getLogger(name).removeHandler(handler)
    _handlers.clear()


def read(owner_id,admin,minutes=30,now=None):
    """The newest scrubbed lines in the last `minutes` minutes. Returns text plus what it covers."""
    now=time.time() if now is None else now
    minutes=max(1,min(int(minutes),240));since=now-minutes*60
    with _lock:rows=list(_lines)
    picked=[r for r in rows if r[0]>=since and (admin or (r[1] is not None and r[1]==owner_id))]
    out=[]
    for stamp,owner,level,name,text in picked:
        when=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        who=('' if not admin else ' [you]' if owner==owner_id else ' [other:%s]'%owner[:8] if owner else ' [system]')
        out.append('%s %-7s %s%s: %s'%(when,level,name,who,scrub(text)))
    return {'text':'\n'.join(out),'count':len(out),'minutes':minutes,'scope':'all accounts and system (operator)' if admin else 'your account only',
            'since':datetime.fromtimestamp(since,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'until':datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'buffer_full':len(rows)>=MAX_LINES,'scrubbed':True}


EXPORT_MESSAGE_CAP=160


def export(minutes,started,now=None):
    """The operator's download: the same scrubbed lines, with two more cuts. Source lines of tracebacks are dropped, and any line from a
    library (not the app's own activity log) is cut to 160 characters, because an exception message can echo a value a user typed
    and no pattern can recognise that. Accounts show as 8 characters. No request bodies, headers or cookies are ever buffered."""
    now=time.time() if now is None else now
    minutes=max(1,min(int(minutes),240));since=now-minutes*60
    with _lock:rows=[r for r in _lines if r[0]>=since]
    out=[]
    for stamp,owner,level,name,text in rows:
        text=scrub(text)
        if text.startswith('    '):continue
        if name!='delivery_api.activity' and len(text)>EXPORT_MESSAGE_CAP:text=text[:EXPORT_MESSAGE_CAP]+' [cut]'
        when=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        who=' [account %s]'%owner[:8] if owner else ' [system]'
        out.append('%s %-7s %s%s: %s'%(when,level,name,who,text))
    fmt=lambda t:datetime.fromtimestamp(t,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    head=['Requi.re server log','Window: %s to %s (%d minutes)'%(fmt(since),fmt(now),minutes),'Lines: %d'%len(out),
          'Source: the memory of this server process, started %s. A restart empties it. Web request access lines are not included.'%fmt(started),
          'Scrubbed: emails, tokens, keys, connection strings and long hex strings are removed. Exception text is cut to %d characters and traceback source lines are dropped.'%EXPORT_MESSAGE_CAP,
          'The buffer keeps the newest %d lines%s.'%(MAX_LINES,'; it is full, so older lines in this window may be missing' if len(_lines)>=MAX_LINES else ''),'']
    return '\n'.join(head+out)+'\n'
