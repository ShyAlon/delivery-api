"""CPU and memory of every run, sampled while it runs and kept for the operator.

A sample is taken every METRICS_INTERVAL_S seconds for the run process group.
PostgreSQL applications always store metrics in PG, with distinct host sources
and registry-authorized cross-host operator reads. The explicit legacy SQLite
profile remains a reference, never a PG failure fallback. Old raw rows become
permanent run/day summaries through bounded metrics-only maintenance, including
retired sources. No history is imported. Sampling failures do not stop a run.
"""
import os,sqlite3,threading,time
from pathlib import Path
from . import config
from .observability import event as activity

PROC_DIR="/proc"
CGROUP_CPU_FILES=(("/sys/fs/cgroup/cpu.stat","v2"),("/sys/fs/cgroup/cpuacct/cpuacct.usage","v1"),("/sys/fs/cgroup/cpu/cpuacct.usage","v1"))
try:CLK_TCK=os.sysconf("SC_CLK_TCK")
except (ValueError,OSError,AttributeError):CLK_TCK=100
try:PAGE_BYTES=os.sysconf("SC_PAGE_SIZE")
except (ValueError,OSError,AttributeError):PAGE_BYTES=4096

SCHEMA="""
CREATE TABLE IF NOT EXISTS run_samples(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_key TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT, phase TEXT,
 pid INTEGER, procs INTEGER, rss_mb REAL, peak_rss_mb REAL, cpu_seconds REAL, cpu_pct REAL);
CREATE INDEX IF NOT EXISTS idx_run_samples_run ON run_samples(run_key,ts);
CREATE INDEX IF NOT EXISTS idx_run_samples_task ON run_samples(task_id,ts);
CREATE TABLE IF NOT EXISTS run_summaries(
 run_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT, phase TEXT, pid INTEGER, started_at REAL, ended_at REAL, samples INTEGER,
 avg_cpu_pct REAL, max_cpu_pct REAL, avg_rss_mb REAL, max_rss_mb REAL, cpu_seconds REAL, rolled_up_at REAL);
CREATE INDEX IF NOT EXISTS idx_run_summaries_task ON run_summaries(task_id);
CREATE TABLE IF NOT EXISTS container_samples(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, mem_used_mb REAL, mem_limit_mb REAL, cpu_seconds REAL, cpu_pct REAL);
CREATE INDEX IF NOT EXISTS idx_container_samples_ts ON container_samples(ts);
CREATE TABLE IF NOT EXISTS container_summaries(
 day TEXT PRIMARY KEY, samples INTEGER, avg_mem_mb REAL, max_mem_mb REAL, avg_cpu_pct REAL, max_cpu_pct REAL, rolled_up_at REAL);
"""

_lock=threading.RLock()
_db=None
_db_path=None
_problem=None
_last_container=None   # (ts, cpu_seconds) of the previous container reading
_idle_stop=None;_idle_thread=None
_last_rollup=0.0


def _pg():
    """PG application owns metrics; no file fallback, including overrides.

    Container series describe one host, unlike the shared process-log ring.
    Reuse a deployment-supplied source where stable host names are unavailable.
    Never blend two hosts' CPU/memory into one time series.
    """
    return selected_metrics(config.DATABASE_URL,os.environ)


def selected_metrics(dsn,env):
    pg=dsn.startswith(('postgres://','postgresql://','host='))
    backend=env.get('DELIVERY_METRICS_BACKEND','postgres' if pg else 'sqlite')
    if pg and backend=='sqlite':raise ValueError('PostgreSQL application requires PostgreSQL metrics')
    if backend=='sqlite':return None
    if backend!='postgres':raise ValueError('unknown metrics backend')
    import socket
    from .postgres_metrics import PostgresMetrics
    source=env.get('DELIVERY_METRICS_SOURCE')
    if source is None:source='host:'+socket.gethostname()
    return PostgresMetrics(dsn,source_id=source)


def db_path():
    """DELIVERY_METRICS_DB when set, else run_metrics.db in the data directory (/data in the server image, so it survives restarts)."""
    explicit=os.environ.get("DELIVERY_METRICS_DB","").strip()
    return Path(explicit) if explicit else config.default_data_dir()/"run_metrics.db"


def reset():
    """Close the file and forget in-memory state (tests)."""
    global _db,_db_path,_problem,_last_container,_last_rollup
    with _lock:
        if _db is not None:
            try:_db.close()
            except Exception:pass
        _db=None;_db_path=None;_problem=None;_last_container=None;_last_rollup=0.0


def _open(create=True):
    global _db,_db_path
    path=db_path()
    with _lock:
        if _db is not None and _db_path==path:return _db
        if _db is not None:
            try:_db.close()
            except Exception:pass
            _db=None
        if not create and not path.exists():return None
        path.parent.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(str(path),check_same_thread=False,timeout=5)
        conn.row_factory=sqlite3.Row
        try:conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:pass
        conn.executescript(SCHEMA)
        _db,_db_path=conn,path
        return conn


def _failed(exc):
    global _problem
    text=type(exc).__name__+": resource metrics operation failed; private driver detail excluded"
    if _problem is None:activity("metrics.unavailable","Run samples could not be stored",error=text)
    _problem=text


def _stat_fields(name):
    """Fields of /proc/<pid>/stat after the command name (which can hold spaces and brackets). None when the process is gone."""
    try:
        with open("%s/%s/stat"%(PROC_DIR,name)) as handle:text=handle.read()
    except OSError:return None
    rest=text.rpartition(")")[2].split()
    try:
        return {"pgrp":int(rest[2]),"utime":int(rest[11]),"stime":int(rest[12]),"cutime":int(rest[13]),"cstime":int(rest[14]),"rss":int(rest[21])}
    except (IndexError,ValueError):return None


def read_group(pid):
    """Memory (MB) and CPU seconds of a child process and everything in its process group. None when /proc has no such process (or is not there)."""
    try:pid=int(pid)
    except (TypeError,ValueError):return None
    try:names=os.listdir(PROC_DIR)
    except OSError:return None
    rss=0;cpu=0.0;procs=0
    for name in names:
        if not name.isdigit():continue
        fields=_stat_fields(name)
        if fields is None:continue
        if int(name)!=pid and fields["pgrp"]!=pid:continue
        procs+=1;rss+=fields["rss"]*PAGE_BYTES;cpu+=(fields["utime"]+fields["stime"])/CLK_TCK
        if int(name)==pid:cpu+=(fields["cutime"]+fields["cstime"])/CLK_TCK   # children that already exited
    if procs==0:return None
    return {"rss_mb":round(rss/1048576,1),"cpu_seconds":round(cpu,2),"procs":procs}


def _container_cpu_seconds():
    for path,kind in CGROUP_CPU_FILES:
        try:
            with open(path) as handle:text=handle.read()
            if kind=="v2":
                for line in text.splitlines():
                    if line.startswith("usage_usec"):return int(line.split()[1])/1e6
            else:return int(text.strip())/1e9
        except (OSError,ValueError,IndexError):continue
    return None


class RunSampler:
    """Samples one run. One per phase run; the key ties its raw rows and its later summary together."""
    def __init__(self,task_id,session_id,phase,started):
        self.task_id=task_id;self.session_id=session_id;self.phase=phase;self.started=started
        self.run_key="%s:%s:%d"%(task_id,phase or "",int(started*1000))
        self.peak=0.0;self._last=None

    def sample(self,pid,now=None):
        """Read the group and store one row. Returns the row, or None when nothing could be read or stored."""
        global _problem
        group=read_group(pid)
        if group is None:return None
        now=time.time() if now is None else now
        base_t,base_cpu=self._last if self._last else (self.started,0.0)
        pct=round(max(0.0,(group["cpu_seconds"]-base_cpu)/(now-base_t)*100),1) if now>base_t else 0.0
        self._last=(now,group["cpu_seconds"]);self.peak=max(self.peak,group["rss_mb"])
        row=(now,self.run_key,self.task_id,self.session_id,self.phase,int(pid),group["procs"],group["rss_mb"],self.peak,group["cpu_seconds"],pct)
        try:
            pg=_pg()
            if pg is not None:
                pg.append_run(**dict(zip(('ts','run_key','task_id','session_id','phase','pid','procs','rss_mb','peak_rss_mb','cpu_seconds','cpu_pct'),row)))
            else:
              with _lock:
                conn=_open()
                with conn:conn.execute("INSERT INTO run_samples(ts,run_key,task_id,session_id,phase,pid,procs,rss_mb,peak_rss_mb,cpu_seconds,cpu_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?)",row)
            _problem=None
        except Exception as exc:
            _failed(exc);return None
        return {"ts":now,"rss_mb":group["rss_mb"],"peak_rss_mb":self.peak,"cpu_seconds":group["cpu_seconds"],"cpu_pct":pct,"procs":group["procs"]}


_container_lock=threading.Lock()


def sample_container(now=None):
    """Store the container's memory and CPU. Called with every run sample and by the idle timer below."""
    global _last_container
    from . import run_slots
    now=time.time() if now is None else now
    host=run_slots.host_capacity()
    if host.get("mem_total_mb") is None:return None
    used=host["mem_total_mb"]-(host.get("mem_available_mb") or 0)
    with _container_lock:      # the run watchdog and the idle timer both come here; a reading and the one before it must stay in order
        cpu=_container_cpu_seconds();pct=None
        if cpu is not None and _last_container is not None and now>_last_container[0]:
            pct=round(max(0.0,(cpu-_last_container[1])/(now-_last_container[0])*100),1)
        if cpu is not None:_last_container=(now,cpu)
    try:
        pg=_pg()
        if pg is not None:
            pg.append_container(ts=now,mem_used_mb=used,mem_limit_mb=host['mem_total_mb'],cpu_seconds=cpu,cpu_pct=pct)
        else:
          with _lock:
            conn=_open()
            with conn:conn.execute("INSERT INTO container_samples(ts,mem_used_mb,mem_limit_mb,cpu_seconds,cpu_pct) VALUES (?,?,?,?,?)",(now,used,host["mem_total_mb"],cpu,pct))
    except Exception as exc:
        _failed(exc);return None
    return {"mem_used_mb":used,"mem_limit_mb":host["mem_total_mb"],"cpu_pct":pct}


def start_idle_sampler(interval_s=None):
    """Read the container's CPU and memory every METRICS_IDLE_INTERVAL_S seconds whether or not a run is active. Safe to call twice. Never raises."""
    global _idle_stop,_idle_thread
    if _idle_thread is not None and _idle_thread.is_alive():return False
    every=config.METRICS_IDLE_INTERVAL_S if interval_s is None else interval_s
    stop=_idle_stop=threading.Event()
    def loop():
        global _last_rollup
        while not stop.is_set():
            try:
                sample_container()
                t=time.time()
                if t-_last_rollup>=3600:_last_rollup=t;rollup(t)
            except Exception as exc:_failed(exc)
            stop.wait(every)
    _idle_thread=threading.Thread(target=loop,name="container-metrics",daemon=True);_idle_thread.start()
    return True


def stop_idle_sampler():
    global _idle_thread
    if _idle_stop is not None:_idle_stop.set()
    if _idle_thread is not None:_idle_thread.join(timeout=2)
    _idle_thread=None


def rollup(now=None,raw_days=None):
    """Replace raw rows older than the retention with permanent summaries, in one transaction. Only runs and days that are entirely older are rolled. Returns (runs, days) rolled."""
    now=time.time() if now is None else now
    cutoff=now-(config.METRICS_RAW_DAYS if raw_days is None else raw_days)*86400
    pg=_pg()
    if pg is not None:return pg.maintain(cutoff=cutoff,now=now)
    with _lock:
        conn=_open()
        with conn:
            runs=conn.execute("SELECT run_key,task_id,session_id,phase,pid,min(ts) a,max(ts) b,count(*) n,avg(cpu_pct) ac,max(cpu_pct) mc,avg(rss_mb) ar,max(rss_mb) mr,max(cpu_seconds) cs "
                              "FROM run_samples GROUP BY run_key HAVING max(ts)<?",(cutoff,)).fetchall()
            for r in runs:
                conn.execute("INSERT OR REPLACE INTO run_summaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["run_key"],r["task_id"],r["session_id"],r["phase"],r["pid"],r["a"],r["b"],r["n"],_r(r["ac"]),_r(r["mc"]),_r(r["ar"]),_r(r["mr"]),r["cs"],now))
            if runs:conn.executemany("DELETE FROM run_samples WHERE run_key=?",[(r["run_key"],) for r in runs])
            days=conn.execute("SELECT strftime('%Y-%m-%d',ts,'unixepoch') d,count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc,min(ts) a,max(ts) b "
                              "FROM container_samples GROUP BY d HAVING max(ts)<?",(cutoff,)).fetchall()
            for d in days:
                conn.execute("INSERT OR REPLACE INTO container_summaries VALUES (?,?,?,?,?,?,?)",(d["d"],d["n"],_r(d["am"]),_r(d["mm"]),_r(d["ac"]),_r(d["mc"]),now))
                conn.execute("DELETE FROM container_samples WHERE strftime('%Y-%m-%d',ts,'unixepoch')=? AND ts<?",(d["d"],cutoff))
    return len(runs),len(days)


def _r(value):return None if value is None else round(value,1)


def tick(info,task_id,pid,store,now=None):
    """One sampling step for a running phase; called by the watchdog every METRICS_INTERVAL_S. Never raises."""
    global _last_rollup
    try:
        sampler=info.get("sampler")
        if sampler is None:
            task={}
            try:task=store.task(task_id) or {} if store is not None else {}
            except Exception:pass
            sampler=info["sampler"]=RunSampler(task_id,task.get("engine_session_id"),task.get("active_phase"),info.get("started") or time.time())
        sampler.sample(pid,now)
        sample_container(now)
        t=time.time() if now is None else now
        if t-_last_rollup>=3600:
            _last_rollup=t;rollup(t)
    except Exception as exc:_failed(exc)


def _rows(conn,sql,args=()):return [dict(r) for r in conn.execute(sql,args).fetchall()]


def _timeline(conn,run_key,points=12):
    values=[r["rss_mb"] for r in conn.execute("SELECT rss_mb FROM run_samples WHERE run_key=? ORDER BY ts",(run_key,)).fetchall()]
    if len(values)>points:values=[values[int(i*(len(values)-1)/(points-1))] for i in range(points)]
    return [round(v) for v in values if v is not None]


def recent_runs(conn,limit=8):
    live=_rows(conn,"SELECT run_key,task_id,session_id,phase,min(ts) started_at,max(ts) ended_at,count(*) samples,avg(cpu_pct) avg_cpu_pct,max(cpu_pct) max_cpu_pct,avg(rss_mb) avg_rss_mb,max(rss_mb) max_rss_mb,max(cpu_seconds) cpu_seconds "
                       "FROM run_samples GROUP BY run_key ORDER BY max(ts) DESC LIMIT ?",(limit,))
    for r in live:r["summary"]=False;r["timeline_mb"]=_timeline(conn,r["run_key"])
    kept=_rows(conn,"SELECT run_key,task_id,session_id,phase,started_at,ended_at,samples,avg_cpu_pct,max_cpu_pct,avg_rss_mb,max_rss_mb,cpu_seconds FROM run_summaries ORDER BY ended_at DESC LIMIT ?",(limit,))
    for r in kept:r["summary"]=True;r["timeline_mb"]=[]
    return sorted(live+kept,key=lambda r:r["ended_at"] or 0,reverse=True)[:limit]


def overview(limit=8):
    """Everything the operator Debug section shows: recent runs, the container's latest and 24-hour figures, and whether storing works."""
    out={"path":str(db_path()),"problem":_problem,"runs":[],"container":None,"raw_days":config.METRICS_RAW_DAYS,"interval_s":config.METRICS_INTERVAL_S}
    try:
        pg=_pg()
        if pg is not None:return pg.overview(limit=limit,problem=_problem,raw_days=config.METRICS_RAW_DAYS,interval_s=config.METRICS_INTERVAL_S)
        with _lock:
            conn=_open(create=False)
            if conn is None:return out
            out["runs"]=recent_runs(conn,limit)
            last=_rows(conn,"SELECT ts,mem_used_mb,mem_limit_mb,cpu_pct FROM container_samples ORDER BY ts DESC LIMIT 1")
            if last:
                day=_rows(conn,"SELECT count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc FROM container_samples WHERE ts>=?",(last[0]["ts"]-86400,))[0]
                out["container"]={"last":last[0],"day":day}
    except Exception as exc:
        _failed(exc);out["problem"]=_problem
    return out


def for_task(task_id,limit=2000):
    """Raw samples and summaries of one task, for the operator."""
    try:
        pg=_pg()
        if pg is not None:return pg.for_task(task_id,limit=limit,problem=_problem)
        with _lock:
            conn=_open(create=False)
            if conn is None:return {"task_id":task_id,"samples":[],"summaries":[],"problem":_problem}
            return {"task_id":task_id,"problem":_problem,
                    "samples":_rows(conn,"SELECT ts,run_key,session_id,phase,pid,procs,rss_mb,peak_rss_mb,cpu_seconds,cpu_pct FROM run_samples WHERE task_id=? ORDER BY ts LIMIT ?",(task_id,limit)),
                    "summaries":_rows(conn,"SELECT * FROM run_summaries WHERE task_id=? ORDER BY started_at",(task_id,))}
    except Exception as exc:
        _failed(exc);return {"task_id":task_id,"samples":[],"summaries":[],"problem":_problem}


def _stamp(ts):return time.strftime("%H:%M",time.localtime(ts)) if ts else "?"


def metrics_text(ov):
    """Plain text for the Debug capacity section. No money wording."""
    lines=["Resource samples: every %d s while a run is active, raw rows kept %d days, then one summary per run. Storage: %s"%(ov["interval_s"],ov["raw_days"],ov["path"])]
    if ov.get("problem"):lines.append("Samples are NOT being stored: %s"%ov["problem"])
    box=ov.get("container")
    if box:
        last,day=box["last"],box["day"]
        cpu="CPU %.0f%% of a core"%last["cpu_pct"] if last["cpu_pct"] is not None else "CPU not readable"
        lines.append("Container at %s: memory %d of %d MB, %s. Last 24 h of samples: memory average %d, maximum %d MB; CPU average %s, maximum %s%%."%(
            _stamp(last["ts"]),last["mem_used_mb"] or 0,last["mem_limit_mb"] or 0,cpu,day["am"] or 0,day["mm"] or 0,
            "%.0f%%"%day["ac"] if day["ac"] is not None else "n/a","%.0f"%day["mc"] if day["mc"] is not None else "n/a"))
    for host in ov.get('containers',[]):
        day=host['day']
        lines.append('Host %s at %s: memory %d of %d MB, CPU %s%% of a core. Last 24 h: memory average %d, maximum %d MB; CPU average %s%%, maximum %s%%.'%(
            host['source_id'],_stamp(host['ts']),host['mem_used_mb'] or 0,host['mem_limit_mb'] or 0,round(host['cpu_pct']) if host['cpu_pct'] is not None else 'n/a',
            day['am'] or 0,day['mm'] or 0,round(day['ac']) if day['ac'] is not None else 'n/a',round(day['mc']) if day['mc'] is not None else 'n/a'))
    if ov.get('truncated'):lines.append('Metrics page is truncated; older runs or additional hosts may be omitted.')
    if not ov["runs"]:
        lines.append("No run has been sampled yet. Samples appear here once a run has been active for a few seconds.")
    for r in ov["runs"]:
        seconds=int((r["ended_at"] or 0)-(r["started_at"] or 0))
        tail="  (summary)" if r["summary"] else ("  memory MB over time: "+" ".join(str(v) for v in r["timeline_mb"]) if r["timeline_mb"] else "")
        lines.append("Run %s / %s, session %s, %ds, %d samples: CPU average %.0f%% maximum %.0f%% of a core (%.0f CPU seconds), memory average %.0f maximum %.0f MB.%s"%(
            r["task_id"],r["phase"] or "?",r["session_id"] or "?",seconds,r["samples"],r["avg_cpu_pct"] or 0,r["max_cpu_pct"] or 0,r["cpu_seconds"] or 0,r["avg_rss_mb"] or 0,r["max_rss_mb"] or 0,tail+(" Host: "+r["source_id"] if r.get("source_id") else "")))
    return "\n".join(lines)
