"""Slots, time limit and peak memory for generator phases that run as child processes.

The slot count is a starting value (config.MAX_CONCURRENT_RUNS). A run that has to wait says so once, in plain words.
The watchdog stops a run that passes the time limit: SIGTERM, then SIGKILL to the child's whole process group.
Peak memory of each child is read from /proc while it runs (Linux) and logged when the run ends."""
import asyncio,signal,time
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
        activity("run.finished","Phase process finished",task_id=task_id,peak_rss_mb=info.get("peak_rss_mb"),timed_out=info.get("timed_out",False),
                 seconds=round(time.time()-info["started"],1) if info.get("started") else None)
        if info.get("timed_out"):
            store.add_event(task_id,"error","This run reached the time limit and was stopped. You can start it again.",phase_key=(store.task(task_id) or {}).get("active_phase"))
        slots.release()
