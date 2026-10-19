"""Slots, time limit and peak memory for generator phases that run as child processes.

The slot count is a starting value (config.MAX_CONCURRENT_RUNS). A run that has to wait says so once, in plain words.
The watchdog stops a run that passes the time limit: SIGTERM, then SIGKILL to the child's whole process group.
Peak memory of each child is read from /proc while it runs (Linux) and logged when the run ends."""
import asyncio,os,signal,time
from . import config
from .observability import event as activity

_slots=None
_slots_size=None
waiting=0
running={}          # task_id -> {"started":epoch,"peak_rss_mb":float}
peaks={}            # task_id -> peak_rss_mb of the last run


def _semaphore():
    global _slots,_slots_size
    if _slots is None or _slots_size!=config.MAX_CONCURRENT_RUNS:
        _slots=asyncio.Semaphore(config.MAX_CONCURRENT_RUNS);_slots_size=config.MAX_CONCURRENT_RUNS
    return _slots


def reset():
    """Forget slots and counters (tests, and a changed MAX_CONCURRENT_RUNS)."""
    global _slots,_slots_size,waiting
    _slots=None;_slots_size=None;waiting=0;running.clear();peaks.clear()


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


async def _watch(orchestrator,task_id,limit_s,tick_s):
    from . import inprocess_engine
    started=time.monotonic();info=running[task_id];stopped=False
    while True:
        await asyncio.sleep(tick_s)
        proc=orchestrator.runner.processes.get(task_id)
        if proc is None or proc.returncode is not None:continue
        peak=peak_rss_mb(proc.pid) if proc.pid else None
        if peak is not None:info["peak_rss_mb"]=max(info["peak_rss_mb"],peak)
        if not stopped and time.monotonic()-started>=limit_s:
            stopped=True;info["timed_out"]=True
            activity("run.time_limit","Phase passed the time limit and is being stopped",task_id=task_id,limit_s=limit_s)
            try:inprocess_engine.terminate(proc,signal.SIGTERM)
            except ProcessLookupError:pass
            kill_at=time.monotonic()+10
        elif stopped and time.monotonic()>=kill_at and proc.returncode is None:
            try:inprocess_engine.terminate(proc,signal.SIGKILL)
            except ProcessLookupError:pass


async def run_limited(store,orchestrator,task_id,work,*,tick_s=2.0):
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
    watcher=asyncio.ensure_future(_watch(orchestrator,task_id,config.PHASE_TIME_LIMIT_S,tick_s))
    try:return await work()
    finally:
        watcher.cancel()
        try:await watcher
        except (asyncio.CancelledError,Exception):pass
        info=running.pop(task_id,{})
        peaks[task_id]=info.get("peak_rss_mb")
        while len(peaks)>50:peaks.pop(next(iter(peaks)))
        activity("run.finished","Phase process finished",task_id=task_id,peak_rss_mb=info.get("peak_rss_mb"),timed_out=info.get("timed_out",False),
                 seconds=round(time.time()-info["started"],1) if info.get("started") else None)
        if info.get("timed_out"):
            store.add_event(task_id,"error","This run reached the time limit and was stopped. You can start it again.",phase_key=(store.task(task_id) or {}).get("active_phase"))
        slots.release()


def host_capacity():
    """CPU count and memory of this machine in MB (from /proc/meminfo on Linux; None where it cannot be read)."""
    out={"cpus":os.cpu_count(),"mem_total_mb":None,"mem_available_mb":None}
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                key,_,value=line.partition(":")
                if key=="MemTotal":out["mem_total_mb"]=round(int(value.split()[0])/1024)
                elif key=="MemAvailable":out["mem_available_mb"]=round(int(value.split()[0])/1024)
    except (OSError,ValueError,IndexError):pass
    return out


def snapshot():
    """What the operator needs to size the server: slots, who is running, peak memory of recent runs, machine memory. No cost or vendor figures."""
    now=time.time()
    return {"isolation":config.RUN_ISOLATION,"max_concurrent_runs":config.MAX_CONCURRENT_RUNS,"time_limit_s":config.PHASE_TIME_LIMIT_S,
        "running":[{"task_id":task,"seconds":round(now-info["started"]),"peak_rss_mb":info["peak_rss_mb"],"timed_out":info["timed_out"]} for task,info in running.items()],
        "waiting":waiting,"recent_peaks":[{"task_id":task,"peak_rss_mb":peak} for task,peak in list(peaks.items())[-10:]],"host":host_capacity()}


def snapshot_text(snap):
    host=snap["host"];lines=[]
    mode="one child process per run" if snap["isolation"]=="process" else "thread mode: one run at a time (the slot count below applies in process mode only)"
    lines.append("Capacity: %s. Slots %d, running %d, waiting %d. Time limit %d min."%(mode,snap["max_concurrent_runs"],len(snap["running"]),snap["waiting"],snap["time_limit_s"]//60))
    if host["mem_total_mb"] is not None:lines.append("Server memory: %d MB total, %d MB available. CPUs: %s."%(host["mem_total_mb"],host["mem_available_mb"] or 0,host["cpus"]))
    else:lines.append("Server memory: not readable on this machine.")
    seen=[p["peak_rss_mb"] for p in snap["recent_peaks"] if p["peak_rss_mb"]]+[r["peak_rss_mb"] for r in snap["running"] if r["peak_rss_mb"]]
    if seen:
        biggest=max(seen);lines.append("Biggest run seen since the server started: %.0f MB."%biggest)
        if host["mem_available_mb"] is not None:lines.append("Room for about %d more runs at that size (a guess from one measurement, not a limit)."%int(host["mem_available_mb"]//biggest))
    else:lines.append("No run has finished since the server started, so there is no memory figure yet. Run one and look again before changing the slot count.")
    for r in snap["running"]:lines.append("Running: %s, %ds, peak %s MB%s"%(r["task_id"],r["seconds"],r["peak_rss_mb"],", being stopped" if r["timed_out"] else ""))
    return "\n".join(lines)
