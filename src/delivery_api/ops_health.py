"""What the operator page shows about the server itself: uptime, runs, CPU, memory, who is signed in, and a log file to download.

Everything here is read from this server process. Uptime and the signed-in list start again at a restart, and the page says so.
No cost, price, vendor or customer-content figures are returned."""
import time
from datetime import datetime,timezone

STARTED=time.time()
ACTIVE_MINUTES=15
_seen={}
_MAX_SEEN=5000
PHASE_LABELS={'intent':'Intent','conops':'Concept Of Operations','needs':'Needs','requirements':'Requirements','architecture':'Architecture'}


def touch(user_id,label=None,email=None,now=None):
    """Remember that this signed-in user made a request just now: when, and the name or email to show the operator. Bounded; an unknown id is ignored.
    Kept in memory only. It is shown on the operator page and is never written to the log or the download."""
    if not user_id or user_id in ('anonymous',):return
    now=time.time() if now is None else now
    _seen[user_id]=(now,label,email)
    if len(_seen)>_MAX_SEEN:
        for key,_ in sorted(_seen.items(),key=lambda kv:kv[1][0])[:len(_seen)-_MAX_SEEN+500]:_seen.pop(key,None)


def active_users(minutes=ACTIVE_MINUTES,now=None):
    now=time.time() if now is None else now
    return sum(1 for v in list(_seen.values()) if v[0]>=now-minutes*60)


def sessions(minutes=ACTIVE_MINUTES,now=None,limit=200):
    """Signed-in users seen in the window, newest first. The user is the email (or the display name when there is none). 'Running' names the
    run that user has going, by phase, never by task title or id."""
    from . import run_slots
    now=time.time() if now is None else now
    by_owner={}
    for i,info in enumerate(list(run_slots.running.values())):
        if info.get('owner_id'):by_owner.setdefault(info['owner_id'],[]).append('Run %d (%s)'%(i+1,_label(info.get('phase'))))
    rows=[]
    for uid,(ts,label,email) in list(_seen.items()):
        if ts<now-minutes*60:continue
        rows.append({'user':email or label or 'Unknown User','last_seen':_iso(ts),'seconds_ago':int(max(0,now-ts)),
                     'running':', '.join(by_owner.get(uid,[])) or 'None'})
    rows.sort(key=lambda r:r['seconds_ago'])
    return rows[:limit]


def reset():
    _seen.clear()


def _iso(ts):
    return datetime.fromtimestamp(ts,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if ts else None


def _label(phase):
    if not phase:return 'Unknown'
    return PHASE_LABELS.get(phase,str(phase).replace('_',' ').title())


def _flags(h):
    out=[]
    mem=h['memory']
    if mem['used_percent'] is not None and mem['used_percent']>=85:
        out.append({'level':'warning','text':'Memory is %d%% used. A new run may fail to start.'%mem['used_percent']})
    cap=h['capacity']
    if cap['waiting']>0:out.append({'level':'info','text':'%d run%s waiting for a free slot.'%(cap['waiting'],'' if cap['waiting']==1 else 's')})
    if h['cpu']['container_percent'] is not None and h['cpu']['cpus'] and h['cpu']['container_percent']>=90*h['cpu']['cpus']:
        out.append({'level':'warning','text':'CPU is above 90% of every core.'})
    if h['metrics_problem']:out.append({'level':'warning','text':'Resource samples are not being stored: %s'%h['metrics_problem']})
    for r in h['running']:
        if r['state']=='Past Time Limit':out.append({'level':'warning','text':'A run passed the time limit and is being stopped.'})
    if not out:out.append({'level':'ok','text':'No problems found.'})
    return out


def health(now=None):
    """One plain snapshot for the operator health page."""
    from . import run_slots,run_metrics
    now=time.time() if now is None else now
    snap=run_slots.snapshot();host=snap['host'];metrics=snap.get('metrics') or {}
    total=host.get('mem_total_mb');avail=host.get('mem_available_mb')
    used=(total-avail) if total is not None and avail is not None else None
    box=(metrics.get('container') or {})
    last=box.get('last') or {};day=box.get('day') or {}
    running=[{'run':'Run %d'%(i+1),'seconds':int(r['seconds']),'peak_memory_mb':r['peak_rss_mb'],
              'state':'Past Time Limit' if r['timed_out'] else 'Stopping' if r.get('stopping') else 'Running'} for i,r in enumerate(snap['running'])]
    runs=[{'phase':_label(r.get('phase')),'started':_iso(r.get('started_at')),'ended':_iso(r.get('ended_at')),
           'avg_cpu_percent':round(r['avg_cpu_pct'],1) if r.get('avg_cpu_pct') is not None else None,
           'max_cpu_percent':round(r['max_cpu_pct'],1) if r.get('max_cpu_pct') is not None else None,
           'peak_memory_mb':round(r['max_rss_mb'],1) if r.get('max_rss_mb') is not None else None,
           'cpu_seconds':round(r['cpu_seconds'],1) if r.get('cpu_seconds') is not None else None,
           'kept_as':'Summary' if r.get('summary') else 'Live Samples'} for r in (metrics.get('runs') or [])]
    h={'as_of':_iso(now),'started':_iso(STARTED),'uptime_seconds':int(max(0,now-STARTED)),
       'restart_note':'Uptime, signed-in users and the log start again when the server restarts.',
       'capacity':{'mode':'One Child Process Per Run' if snap['isolation']=='process' else 'Thread Mode: One Run At A Time','slots':snap['max_concurrent_runs'],
                   'running':len(snap['running']),'waiting':snap['waiting'],'time_limit_minutes':snap['time_limit_s']//60},
       'signed_in_users':active_users(now=now),'sessions':sessions(now=now),'signed_in_window_minutes':ACTIVE_MINUTES,
       'memory':{'total_mb':total,'available_mb':avail,'used_mb':used,'used_percent':round(100*used/total) if used is not None and total else None,
                 'limited_by':'Container' if host.get('limited_by')=='container' else 'Machine','container_average_24h_mb':round(day['am']) if day.get('am') is not None else None,
                 'container_peak_24h_mb':round(day['mm']) if day.get('mm') is not None else None},
       'cpu':{'cpus':host.get('cpus'),'container_percent':last.get('cpu_pct'),'average_24h_percent':round(day['ac'],1) if day.get('ac') is not None else None,
              'peak_24h_percent':round(day['mc'],1) if day.get('mc') is not None else None},
       'metrics_problem':metrics.get('problem'),'running':running,'recent_runs':runs}
    h['flags']=_flags(h)
    return h


def log_download(minutes=60,now=None):
    """The server log as a plain-text file body, and its file name. Stricter than the Debug view: see server_log_buffer.export."""
    from . import server_log_buffer
    now=time.time() if now is None else now
    body=server_log_buffer.export(minutes,STARTED,now)
    name='requi-server-log-%s.txt'%datetime.fromtimestamp(now,timezone.utc).strftime('%Y%m%d-%H%M%SZ')
    return name,body
