"""Slots, time limit and peak memory for generator phases that run as child processes.

The slot count is a starting value (config.MAX_CONCURRENT_RUNS). A run that has to wait says so once, in plain words.
The watchdog stops a run that passes the time limit: SIGTERM, then SIGKILL to the child's whole process group.
Peak memory of each child is read from /proc while it runs (Linux) and logged when the run ends."""
import asyncio,os,signal,time
from . import config
from .observability import event as activity
from . import run_metrics

_slots=None
_slots_size=None
waiting=0
waiting_memory=0     # runs holding a slot that are waiting for free memory
MEMORY_POLL_S=2.0
running={}          # task_id -> {"started":epoch,"peak_rss_mb":float}
peaks={}            # task_id -> peak_rss_mb of the last run


def _semaphore():
    global _slots,_slots_size
    if _slots is None or _slots_size!=config.MAX_CONCURRENT_RUNS:
        _slots=asyncio.Semaphore(config.MAX_CONCURRENT_RUNS);_slots_size=config.MAX_CONCURRENT_RUNS
    return _slots


def reset():
    """Forget slots and counters (tests, and a changed MAX_CONCURRENT_RUNS)."""
    global _slots,_slots_size,waiting,waiting_memory
    _slots=None;_slots_size=None;waiting=0;waiting_memory=0;running.clear();peaks.clear()


def peak_rss_mb(pid):
    """Peak resident memory of a process in MB: VmHWM from /proc on Linux. Elsewhere (macOS) the current size from ps, the watchdog keeps the maximum of its samples. None when unreadable."""
    try:
        with open(f"/proc/{int(pid)}/status") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):return round(int(line.split()[1])/1024,1)
        return None
    except (OSError,ValueError,TypeError):pass
    try:
        import subprocess
        out=subprocess.run(["ps","-o","rss=","-p",str(int(pid))],capture_output=True,text=True,timeout=2).stdout.strip()
        return round(int(out)/1024,1) if out else None
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):return None


async def _watch(orchestrator,task_id,limit_s,tick_s,store=None):
    from . import inprocess_engine
    started=time.monotonic();info=running[task_id];stopped=False;next_sample=started
    while True:
        await asyncio.sleep(tick_s)
        proc=orchestrator.runner.processes.get(task_id)
        if proc is None or proc.returncode is not None:continue
        peak=peak_rss_mb(proc.pid) if proc.pid else None
        if peak is not None:info["peak_rss_mb"]=max(info["peak_rss_mb"],peak)
        if proc.pid and time.monotonic()>=next_sample:
            next_sample=time.monotonic()+config.METRICS_INTERVAL_S
            run_metrics.tick(info,task_id,proc.pid,store)   # CPU and memory samples for the operator; never raises
        if not stopped and not info.get("stop_by"):_memory_guard(task_id,info)
        by_request=bool(info.get("stop_by"))
        if not stopped and (by_request or time.monotonic()-started>=limit_s):
            stopped=True
            if by_request:activity("run.stop","Phase is being stopped on request" if info["stop_by"]!="memory" else "Phase is being stopped because the server is out of memory",task_id=task_id,by=info["stop_by"])
            else:
                info["timed_out"]=True
                activity("run.time_limit","Phase passed the time limit and is being stopped",task_id=task_id,limit_s=limit_s)
            try:inprocess_engine.terminate(proc,signal.SIGTERM)
            except ProcessLookupError:pass
            kill_at=time.monotonic()+10
        elif stopped and time.monotonic()>=kill_at and proc.returncode is None:
            try:inprocess_engine.terminate(proc,signal.SIGKILL)
            except ProcessLookupError:pass


async def run_limited(store,orchestrator,task_id,work,*,tick_s=0.25):
    """Wait for a free slot, run work() with the watchdog, release the slot even when work fails."""
    global waiting
    slots=_semaphore()
    if slots.locked():
        store.add_event(task_id,"progress","Another run is using the server right now. Yours is next and starts automatically.",phase_key=(store.task(task_id) or {}).get("active_phase"))
        activity("run.waiting","Waiting for a free run slot",task_id=task_id,slots=config.MAX_CONCURRENT_RUNS)
    waiting+=1
    try:await slots.acquire()
    finally:waiting-=1
    running[task_id]={"started":time.time(),"peak_rss_mb":0.0,"timed_out":False}
    try:   # who runs it and which phase, for the operator health page only (never shown to customers)
        from .tenancy import current_scope
        running[task_id]["owner_id"]=current_scope().owner_id;running[task_id]["phase"]=(store.task(task_id) or {}).get("active_phase")
    except Exception:pass
    watcher=None
    try:
        await _wait_for_memory(store,task_id)
        watcher=asyncio.ensure_future(_watch(orchestrator,task_id,config.PHASE_TIME_LIMIT_S,tick_s,store))
        return await work()
    finally:
        if watcher is not None:
            watcher.cancel()
            try:await watcher
            except (asyncio.CancelledError,Exception):pass
        info=running.pop(task_id,{})
        peaks[task_id]=info.get("peak_rss_mb")
        while len(peaks)>50:peaks.pop(next(iter(peaks)))
        activity("run.finished","Phase process finished",task_id=task_id,peak_rss_mb=info.get("peak_rss_mb"),timed_out=info.get("timed_out",False),
                 seconds=round(time.time()-info["started"],1) if info.get("started") else None,cpu_seconds=info.get("cpu_seconds"))
        if info.get("timed_out"):
            store.add_event(task_id,"error","This run reached the time limit and was stopped. You can start it again.",phase_key=(store.task(task_id) or {}).get("active_phase"))
        elif info.get("stop_by"):
            who="You stopped this run." if info["stop_by"]=="owner" else "This run was stopped because the server ran out of memory." if info["stop_by"]=="memory" else "An operator stopped this run."
            store.add_event(task_id,"progress",who+" You can start it again.",phase_key=(store.task(task_id) or {}).get("active_phase"))
            try:store.audit(info.get("stop_label") or info["stop_by"],"run.stopped",task_id,{"by":info["stop_by"],"peak_rss_mb":info.get("peak_rss_mb"),"phase":info.get("phase")})
            except Exception as exc:activity("run.audit_failed","The stop was done but its audit entry could not be written",task_id=task_id,error=type(exc).__name__)
        slots.release()


MEMINFO_FILE="/proc/meminfo"
CGROUP_STAT_FILES=("/sys/fs/cgroup/memory.stat","/sys/fs/cgroup/memory/memory.stat")
CGROUP_MEMORY_FILES=(("/sys/fs/cgroup/memory.max","/sys/fs/cgroup/memory.current"),
                     ("/sys/fs/cgroup/memory/memory.limit_in_bytes","/sys/fs/cgroup/memory/memory.usage_in_bytes"))


def _container_memory_mb():
    """(limit_mb, used_mb) of this container's memory cgroup, or None when there is no limit or it cannot be read."""
    for limit_path,used_path in CGROUP_MEMORY_FILES:
        try:
            with open(limit_path) as handle:raw=handle.read().strip()
            if raw=="max":continue
            limit=int(raw)
            with open(used_path) as handle:used=int(handle.read().strip())
        except (OSError,ValueError):continue
        if limit<=0 or limit>=1<<60:continue
        return round(limit/1048576),round(used/1048576)
    return None


def host_capacity():
    """CPU count and memory in MB: /proc/meminfo on Linux, replaced by the container limit when the service runs in a memory-limited container. None where it cannot be read."""
    out={"cpus":os.cpu_count(),"mem_total_mb":None,"mem_available_mb":None}
    try:
        with open(MEMINFO_FILE) as handle:
            for line in handle:
                key,_,value=line.partition(":")
                if key=="MemTotal":out["mem_total_mb"]=round(int(value.split()[0])/1024)
                elif key=="MemAvailable":out["mem_available_mb"]=round(int(value.split()[0])/1024)
    except (OSError,ValueError,IndexError):pass
    box=_container_memory_mb()
    if box is not None and (out["mem_total_mb"] is None or box[0]<out["mem_total_mb"]):
        # Inside a container /proc/meminfo shows the whole machine; the container limit is what the service and its runs really share.
        out["mem_total_mb"]=box[0];out["mem_available_mb"]=max(0,box[0]-box[1]);out["limited_by"]="container"
    return out


def _working_set_credit_mb():
    """Memory the container holds only as reclaimable file cache (inactive_file). The kernel gives it back under pressure, so it is not a reason to stop a run. 0 when unreadable."""
    for path,key in ((CGROUP_STAT_FILES[0],"inactive_file"),(CGROUP_STAT_FILES[1],"total_inactive_file")):
        try:
            with open(path) as handle:
                for line in handle:
                    name,_,value=line.partition(" ")
                    if name==key:return int(value)//1048576
        except (OSError,ValueError):continue
    return 0


def memory_state():
    """{"total_mb","available_mb","used_percent"} for the memory the runs share, or None where it cannot be read. Reclaimable cache counts as available."""
    host=host_capacity()
    total,avail=host.get("mem_total_mb"),host.get("mem_available_mb")
    if not total or avail is None:return None
    if host.get("limited_by")=="container":avail=min(total,avail+_working_set_credit_mb())
    return {"total_mb":total,"available_mb":avail,"used_percent":round(100*(total-avail)/total)}


def memory_needed_mb():
    """Free memory a new run needs: the larger of the floor and the factor times the biggest run seen recently (finished or running)."""
    seen=[p for p in peaks.values() if p]+[i.get("peak_rss_mb") or 0 for i in running.values()]
    return max(config.MEMORY_START_FLOOR_MB,round(config.MEMORY_START_FACTOR*max(seen,default=0)))


async def _wait_for_memory(store,task_id):
    """Hold a new run (it already has its slot) until there is room, but never longer than MEMORY_WAIT_LIMIT_S and never when no other run is going,
    because then waiting cannot help. After the limit the run starts anyway; the stop guard below protects the server."""
    global waiting_memory
    info=running.get(task_id)
    started=time.monotonic();counted=False
    try:
        while True:
            others=[t for t in running if t!=task_id]
            state=memory_state();need=memory_needed_mb()
            if state is None or not others or state["available_mb"]>=need:
                if counted:activity("run.memory_ready","Enough free memory; the waiting run starts",task_id=task_id,waited_s=round(time.monotonic()-started,1),available_mb=state and state["available_mb"],needed_mb=need)
                return
            waited=time.monotonic()-started
            if waited>=config.MEMORY_WAIT_LIMIT_S:
                activity("run.memory_wait_over","Still short of memory after the wait limit; the run starts anyway and the memory stop guard watches it",task_id=task_id,
                         waited_s=round(waited),available_mb=state["available_mb"],needed_mb=need,used_percent=state["used_percent"],running=len(others))
                return
            if not counted:
                counted=True;waiting_memory+=1
                if info is not None:info["waiting_memory"]=True
                try:store.add_event(task_id,"progress","The server is short on memory right now. Your run starts by itself when there is room.",phase_key=(store.task(task_id) or {}).get("active_phase"))
                except Exception:pass
                activity("run.memory_wait","Run is waiting for free memory",task_id=task_id,available_mb=state["available_mb"],needed_mb=need,total_mb=state["total_mb"],
                         used_percent=state["used_percent"],running=len(others),floor_mb=config.MEMORY_START_FLOOR_MB,factor=config.MEMORY_START_FACTOR,wait_limit_s=config.MEMORY_WAIT_LIMIT_S)
            await asyncio.sleep(MEMORY_POLL_S)
    finally:
        if counted:waiting_memory-=1
        if info is not None:info.pop("waiting_memory",None)


def _memory_guard(task_id,info):
    """When memory use passes MEMORY_STOP_PERCENT, ask the watchdog to stop the biggest running run (ties: lowest task id), one at a time."""
    now=time.monotonic()
    if now-info.get("memory_checked",0.0)<1.0:return
    info["memory_checked"]=now
    state=memory_state()
    if state is None or state["used_percent"]<config.MEMORY_STOP_PERCENT:return
    live={t:i for t,i in running.items() if not i.get("waiting_memory")}
    if any(i.get("stop_by")=="memory" for i in live.values()):return   # one stop at a time, then look again
    victim=min(live,key=lambda t:(-(live[t].get("peak_rss_mb") or 0),t))
    if victim!=task_id:return
    info["stop_by"]="memory";info["stop_label"]="Server (Memory)"
    activity("run.memory_stop","Server memory is nearly full; the biggest run is being stopped",task_id=task_id,used_percent=state["used_percent"],available_mb=state["available_mb"],
             total_mb=state["total_mb"],stop_percent=config.MEMORY_STOP_PERCENT,this_peak_mb=info.get("peak_rss_mb"),
             runs={t:i.get("peak_rss_mb") for t,i in live.items()})


def snapshot(*,metrics=None):
    """What the operator needs to size the server: slots, who is running, peak memory of recent runs, machine memory. No cost or vendor figures."""
    now=time.time()
    return {"isolation":config.RUN_ISOLATION,"max_concurrent_runs":config.MAX_CONCURRENT_RUNS,"time_limit_s":config.PHASE_TIME_LIMIT_S,
        "running":[{"task_id":task,"seconds":round(now-info["started"]),"peak_rss_mb":info["peak_rss_mb"],"timed_out":info["timed_out"],"stopping":bool(info.get("stop_by")),"waiting_memory":bool(info.get("waiting_memory"))} for task,info in running.items()],
        "waiting":waiting,"waiting_memory":waiting_memory,"recent_peaks":[{"task_id":task,"peak_rss_mb":peak} for task,peak in list(peaks.items())[-10:]],"host":host_capacity(),"metrics":run_metrics.overview() if metrics is None else metrics}


def snapshot_text(snap):
    host=snap["host"];lines=[]
    mode="one child process per run" if snap["isolation"]=="process" else "thread mode: one run at a time (the slot count below applies in process mode only)"
    lines.append("Capacity: %s. Slots %d, running %d, waiting %d (waiting for memory %d). Time limit %d min."%(mode,snap["max_concurrent_runs"],len(snap["running"]),snap["waiting"],snap.get("waiting_memory",0),snap["time_limit_s"]//60))
    if host["mem_total_mb"] is not None:lines.append("Server memory: %d MB%s, %d MB available. CPUs: %s."%(host["mem_total_mb"]," (container limit)" if host.get("limited_by")=="container" else " total",host["mem_available_mb"] or 0,host["cpus"]))
    else:lines.append("Server memory: not readable on this machine.")
    seen=[p["peak_rss_mb"] for p in snap["recent_peaks"] if p["peak_rss_mb"]]+[r["peak_rss_mb"] for r in snap["running"] if r["peak_rss_mb"]]
    if seen:
        biggest=max(seen);lines.append("Biggest run seen since the server started: %.0f MB."%biggest)
        if host["mem_available_mb"] is not None:lines.append("Room for about %d more runs at that size (a guess from one measurement, not a limit)."%int(host["mem_available_mb"]//biggest))
    else:lines.append("No run has finished since the server started, so there is no memory figure yet. Run one and look again before changing the slot count.")
    for r in snap["running"]:lines.append("Running: %s, %ds, peak %s MB%s"%(r["task_id"],r["seconds"],r["peak_rss_mb"],", being stopped" if r["timed_out"] or r.get("stopping") else ""))
    if snap.get("metrics"):lines.append(run_metrics.metrics_text(snap["metrics"]))
    return "\n".join(lines)


def is_running(task_id):
    """True while a phase of this task holds a slot in this server process."""
    return task_id in running


def request_stop(task_id,by,label=None):
    """Ask the watchdog to stop a running phase (SIGTERM, then SIGKILL after 10 s). by is "owner" or "operator". False when nothing of this task is running."""
    info=running.get(task_id)
    if info is None:return False
    if not info.get("stop_by"):info["stop_by"]=by;info["stop_label"]=label
    return True
