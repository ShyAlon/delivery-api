"""What the service knows about the vendor's published terms, for the operator and the log.

Refresh failures are warnings below 24 hours; vendor_sources blocks stale failed refreshes.
A warning stays until a later check succeeds.
"""
import datetime,sys,threading

STALE_HOURS=24
_LOCK=threading.Lock()
_WARNINGS={}
_STATE={'model':None,'pinned_fetched_at':None,'last_verified_at':None}
_LOGGED={}


def _now():return datetime.datetime.now(datetime.timezone.utc)


def _iso(value):return value.isoformat() if value else None


def pin(snapshot):
    """Remember which snapshot the server started with."""
    with _LOCK:
        _STATE['model']=snapshot.get('model')
        _STATE['pinned_fetched_at']=snapshot.get('fetched_at')


def warn(kind,text,*,log=None):
    """Record a warning (first seen, last seen, count). The log line repeats at most once every 10 minutes per kind."""
    now=_now()
    with _LOCK:
        entry=_WARNINGS.setdefault(kind,{'kind':kind,'first_seen':_iso(now),'count':0})
        entry.update(text=text,last_seen=_iso(now));entry['count']+=1
        due=kind not in _LOGGED or (now-_LOGGED[kind]).total_seconds()>=600
        if due:_LOGGED[kind]=now
    if due:
        from .diagnostic_evidence import record
        record('vendor.terms.warning',kind=kind)
        line=_iso(now)+' WARNING vendor terms ('+kind+'): '+text
        (log or (lambda t:print(t,file=sys.stderr,flush=True)))(line)


def verified(snapshot=None):
    """A check against the live pages passed: every earlier failure warning is over."""
    with _LOCK:
        _STATE['last_verified_at']=_iso(_now())
        if snapshot is not None:_STATE['model']=snapshot.get('model')
        _WARNINGS.clear();_LOGGED.clear()


def status():
    """Plain facts for the operator. 'warning' also covers a pin older than 24 h that no live check has renewed."""
    now=_now()
    with _LOCK:
        warnings=[dict(v) for v in _WARNINGS.values()]
        state=dict(_STATE)
    newest=state['last_verified_at'] or state['pinned_fetched_at']
    if newest:
        age=now-datetime.datetime.fromisoformat(newest.replace('Z','+00:00'))
        if age>=datetime.timedelta(hours=STALE_HOURS):
            warnings.append({'kind':'terms-not-verified','text':'The vendor prices and limits have not been confirmed for %d hours. A failed refresh now blocks new calls.'%(age.total_seconds()//3600),'first_seen':None,'last_seen':_iso(now),'count':1})
    elif state['model']:
        warnings.append({'kind':'terms-not-verified','text':'The vendor prices and limits have not been confirmed yet.','first_seen':None,'last_seen':_iso(now),'count':1})
    return {'ok':not warnings,'as_of':_iso(now),'model':state['model'],'pinned_fetched_at':state['pinned_fetched_at'],'last_verified_at':state['last_verified_at'],'warnings':warnings}


def reset():
    with _LOCK:
        _WARNINGS.clear();_LOGGED.clear();_STATE.update(model=None,pinned_fetched_at=None,last_verified_at=None)
