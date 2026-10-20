"""Memory guard: new runs wait for free memory, the biggest run is stopped when memory is nearly full, and both show in the Debug log."""
import asyncio
import signal
import sys
from types import SimpleNamespace as NS

import pytest

from delivery_api import config, ops_health, run_slots, server_log_buffer
from delivery_api.tenancy import tenant_context
from test_postgres_store import A, B

GB = 1024


class Store:
    def __init__(self):
        self.events, self.audits = [], []

    def add_event(self, task_id, kind, text, **kw):
        self.events.append((task_id, kind, text))

    def task(self, task_id):
        return {"active_phase": "needs"}

    def audit(self, actor, action, task_id, detail):
        self.audits.append((actor, action, task_id, detail))


def host(total=4 * GB, avail=3 * GB, container=True):
    return lambda: {"cpus": 2, "mem_total_mb": total, "mem_available_mb": avail, **({"limited_by": "container"} if container else {})}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    run_slots.reset()
    monkeypatch.setattr(run_slots, "MEMORY_POLL_S", 0.02)
    monkeypatch.setattr(run_slots, "CGROUP_STAT_FILES", ("/nonexistent/a", "/nonexistent/b"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 3)
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 1800)
    server_log_buffer.install()
    yield
    run_slots.reset()


def test_a_new_run_needs_the_larger_of_the_floor_and_a_quarter_more_than_the_biggest_recent_run():
    assert run_slots.memory_needed_mb() == 600
    run_slots.peaks["old"] = 400
    assert run_slots.memory_needed_mb() == 600        # 1.25 x 400 = 500, under the floor
    run_slots.peaks["big"] = 1000
    run_slots.running["live"] = {"peak_rss_mb": 1200.0}
    assert run_slots.memory_needed_mb() == 1500       # 1.25 x the biggest, running or finished


def test_cache_the_kernel_gives_back_does_not_count_as_used(monkeypatch, tmp_path):
    stat = tmp_path / "memory.stat"
    stat.write_text("anon 100\ninactive_file %d\nactive_file 5\n" % (1500 * 1024 * 1024))
    monkeypatch.setattr(run_slots, "CGROUP_STAT_FILES", (str(stat), "/nonexistent"))
    monkeypatch.setattr(run_slots, "host_capacity", host(total=4 * GB, avail=200))      # looks 95% used
    assert run_slots.memory_state() == {"total_mb": 4 * GB, "available_mb": 1700, "used_percent": 58}
    monkeypatch.setattr(run_slots, "host_capacity", lambda: {"cpus": 1, "mem_total_mb": None, "mem_available_mb": None})
    assert run_slots.memory_state() is None           # not readable (a Mac): the guard stays out of the way


async def hold_run(store, task_id, release, started=None):
    async def work():
        if started:started.set()
        await release.wait()
        return True
    return await run_slots.run_limited(store, NS(runner=NS(processes={})), task_id, work, tick_s=0.05)


def test_a_second_run_waits_for_memory_says_so_once_and_starts_when_there_is_room(monkeypatch):
    store = Store()
    state = {"avail": 300}
    monkeypatch.setattr(run_slots, "host_capacity", lambda: host(avail=state["avail"])())
    log = []

    async def go():
        release, first_up = asyncio.Event(), asyncio.Event()
        a = asyncio.ensure_future(hold_run(store, "first", release, first_up))
        await first_up.wait()
        b_started = asyncio.Event()
        b = asyncio.ensure_future(hold_run(store, "second", asyncio.Event(), b_started))
        await asyncio.sleep(0.2)
        assert not b_started.is_set() and run_slots.waiting_memory == 1
        assert run_slots.snapshot()["waiting_memory"] == 1 and [r["waiting_memory"] for r in run_slots.snapshot()["running"]].count(True) == 1
        log.append(ops_health.health()["running"])
        state["avail"] = 2000
        await asyncio.wait_for(b_started.wait(), 3)
        assert run_slots.waiting_memory == 0
        for t in (a, b):
            t.cancel()
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)
    asyncio.run(go())
    plain = [e for e in store.events if e[0] == "second"]
    assert plain == [("second", "progress", "The server is short on memory right now. Your run starts by itself when there is room.")]   # once, plain words
    assert "Waiting For Memory" in [r["state"] for r in log[0]]


def test_the_wait_ends_after_the_limit_and_a_lone_run_never_waits(monkeypatch):
    store = Store()
    monkeypatch.setattr(run_slots, "host_capacity", host(avail=100))          # far below any need
    monkeypatch.setattr(config, "MEMORY_WAIT_LIMIT_S", 0.15)

    async def go():
        assert await hold_run(store, "lone", _set()) is True                   # nothing else running: waiting cannot help
        release, up = asyncio.Event(), asyncio.Event()
        a = asyncio.ensure_future(hold_run(store, "a", release, up))
        await up.wait()
        started = asyncio.Event()
        b = asyncio.ensure_future(hold_run(store, "b", release, started))
        await asyncio.wait_for(started.wait(), 3)                              # started anyway after the limit
        release.set()
        await asyncio.gather(a, b)
    asyncio.run(go())
    assert [e for e in store.events if e[0] == "lone"] == []


def _set():
    e = asyncio.Event()
    e.set()
    return e


def test_unreadable_memory_means_no_waiting(monkeypatch):
    monkeypatch.setattr(run_slots, "host_capacity", lambda: {"cpus": 1, "mem_total_mb": None, "mem_available_mb": None})

    async def go():
        release, up = asyncio.Event(), asyncio.Event()
        a = asyncio.ensure_future(hold_run(Store(), "a", release, up))
        await up.wait()
        started = asyncio.Event()
        b = asyncio.ensure_future(hold_run(Store(), "b", release, started))
        await asyncio.wait_for(started.wait(), 3)
        release.set()
        await asyncio.gather(a, b)
    asyncio.run(go())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="child processes are measured through /proc")
@pytest.mark.parametrize("used_percent,stopped", [(95, True), (80, False)])
def test_the_biggest_run_is_stopped_at_the_limit_and_the_others_keep_going(monkeypatch, used_percent, stopped):
    store = Store()
    # Memory stays full until the first stop, then frees up, as it does when the biggest run's process group exits.
    monkeypatch.setattr(run_slots, "host_capacity", lambda: host(total=1000, avail=1000 if store.audits else 1000 - used_percent * 10)())
    monkeypatch.setattr(config, "MEMORY_START_FLOOR_MB", 0)
    results = {}

    async def go():
        procs = {}
        orch = NS(runner=NS(processes=procs))

        async def run(task_id, peak, linger):
            async def work():
                procs[task_id] = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time;time.sleep(30)", start_new_session=True)
                run_slots.running[task_id]["peak_rss_mb"] = peak
                run_slots.running[task_id]["phase"] = "needs"
                try:
                    await asyncio.wait_for(procs[task_id].wait(), linger)
                except asyncio.TimeoutError:
                    procs[task_id].send_signal(signal.SIGKILL)
                    await procs[task_id].wait()
                results[task_id] = procs[task_id].returncode
                return True
            return await run_slots.run_limited(store, orch, task_id, work, tick_s=0.05)
        with tenant_context(A):
            await asyncio.gather(run("small", 100.0, 2.5), run("big", 900.0, 2.5), run("mid", 300.0, 2.5))
    asyncio.run(go())
    stopped_now = [t for t, code in results.items() if code in (-signal.SIGTERM, -signal.SIGKILL) and any(a[2] == t for a in store.audits)]
    if stopped:
        assert stopped_now == ["big"]                                            # only the biggest
        mine = server_log_buffer.read(A.owner_id, False, 5)["text"]              # raw numbers in the owner's Debug log
        assert "run.memory_stop" in mine and "used_percent='95'" in mine.replace('"', "'") and "stop_percent='92'" in mine.replace('"', "'") and "this_peak_mb='900.0'" in mine.replace('"', "'")
        assert [a[3]["by"] for a in store.audits] == ["memory"] and store.audits[0][0] == "Server (Memory)" and store.audits[0][1] == "run.stopped"
        text = [e[2] for e in store.events if e[0] == "big"]
        assert "This run was stopped because the server ran out of memory. You can start it again." in text
        assert not any("memory" in e[2] for e in store.events if e[0] in ("small", "mid") and e[1] == "progress" and "stopped" in e[2])
    else:
        assert store.audits == [] and stopped_now == []


def test_memory_wait_and_stop_are_in_the_owners_debug_log_with_raw_numbers_and_not_in_anyone_elses(monkeypatch):
    store = Store()
    monkeypatch.setattr(run_slots, "host_capacity", host(total=8000, avail=1500))      # 81% used: short of the 2000 needed, under the stop line
    monkeypatch.setattr(config, "MEMORY_START_FLOOR_MB", 2000)
    monkeypatch.setattr(config, "MEMORY_WAIT_LIMIT_S", 0.1)

    async def go():
        with tenant_context(A):
            release, up = asyncio.Event(), asyncio.Event()
            a = asyncio.ensure_future(hold_run(store, "dbg-a", release, up))
            await up.wait()
            started = asyncio.Event()
            b = asyncio.ensure_future(hold_run(store, "dbg-b", release, started))
            await asyncio.wait_for(started.wait(), 3)
            release.set()
            await asyncio.gather(a, b)
    asyncio.run(go())
    mine = server_log_buffer.read(A.owner_id, False, 5)["text"]
    assert "run.memory_wait " in mine.replace(":", " ") or "Run is waiting for free memory" in mine
    flat = mine.replace('"', "'")
    assert "available_mb='1500'" in flat and "needed_mb='2000'" in flat and "total_mb='8000'" in flat and "wait_limit_s='0.1'" in flat
    assert "Still short of memory after the wait limit" in mine
    assert "dbg-b" not in server_log_buffer.read(B.owner_id, False, 5)["text"]   # another customer sees none of it


def test_server_health_shows_runs_waiting_for_memory():
    flags = ops_health._flags({'memory': {'used_percent': 40}, 'capacity': {'waiting': 0, 'waiting_memory': 2}, 'cpu': {'container_percent': 5, 'cpus': 2},
                               'metrics_problem': None, 'running': []})
    assert [f['text'] for f in flags] == ['2 runs waiting for free memory.'] and flags[0]['level'] == 'warning'


from test_operator_stop_audit import stops, read  # noqa: E402,F401


def test_the_operator_sees_a_memory_stop_as_stopped_by_the_server(stops):
    store, tag = stops
    with tenant_context(A):
        store.audit("Server (Memory)", "run.stopped", None, {"by": "memory", "peak_rss_mb": 1500.0, "phase": "needs"})
    rows = [r for r in read(store)["stops"] if r["peak_memory_mb"] == 1500.0]
    assert rows and rows[0]["stopped_by"] == "Server (Memory)" and rows[0]["operator"] is None and rows[0]["phase"] == "needs"
