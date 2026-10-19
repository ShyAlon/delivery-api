"""Child-process runs: a slot cap with a plain waiting line, a time limit that kills the whole process group, peak memory."""
import asyncio
import os
import sys
from types import SimpleNamespace as NS

import pytest

from delivery_api import config, run_slots, tenant_jobs


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    run_slots.reset()
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 1800)
    yield
    run_slots.reset()


def _store(events):
    return NS(add_event=lambda *a, **k: events.append((a, k)), task=lambda task_id: {"active_phase": "needs"})


def _orchestrator(processes=None):
    return NS(runner=NS(processes=processes if processes is not None else {}))


def test_defaults_are_process_mode_two_slots_and_thirty_minutes():
    import importlib
    import delivery_api.config as fresh
    importlib.reload(fresh)
    assert fresh.RUN_ISOLATION == "process" and fresh.MAX_CONCURRENT_RUNS == 2 and fresh.PHASE_TIME_LIMIT_S == 1800
    importlib.reload(fresh)


def test_at_most_the_cap_runs_at_once_and_the_third_waits_with_one_plain_line():
    events, active, most = [], [0], [0]
    async def work():
        active[0] += 1; most[0] = max(most[0], active[0])
        await asyncio.sleep(0.05)
        active[0] -= 1
        return True
    async def go():
        return await asyncio.gather(*(run_slots.run_limited(_store(events), _orchestrator(), f"t{i}", work, tick_s=0.01) for i in range(3)))
    assert asyncio.run(go()) == [True, True, True]
    assert most[0] == 2
    waits = [e for e in events if "next and starts automatically" in e[0][2]]
    assert len(waits) == 1 and not any(word in waits[0][0][2].lower() for word in ("expense", "vendor", "provider"))
    assert run_slots.running == {} and run_slots.waiting == 0


def test_a_failing_run_frees_its_slot():
    async def boom():
        raise RuntimeError("child exploded")
    async def go():
        with pytest.raises(RuntimeError):
            await run_slots.run_limited(_store([]), _orchestrator(), "t1", boom, tick_s=0.01)
        return await run_slots.run_limited(_store([]), _orchestrator(), "t2", lambda: asyncio.sleep(0, result="ok"), tick_s=0.01)
    assert asyncio.run(go()) == "ok"
    assert run_slots.running == {}


@pytest.mark.skipif(os.name != "posix", reason="process groups need posix")
def test_a_run_past_the_time_limit_is_stopped_and_the_user_is_told_plainly(monkeypatch):
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 0.3)
    events, procs = [], {}
    async def go():
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True)
        procs["t1"] = child
        async def work():
            await child.wait()
            return True
        await run_slots.run_limited(_store(events), _orchestrator(procs), "t1", work, tick_s=0.05)
        return child.returncode
    code = asyncio.run(go())
    assert code is not None and code != 0
    assert any("reached the time limit and was stopped" in e[0][2] for e in events)


def test_peak_memory_is_read_from_proc():
    assert run_slots.peak_rss_mb(os.getpid()) > 0
    assert run_slots.peak_rss_mb(99999999) is None


def _jobs_store():
    return NS(worker_job=lambda job_id: None, enqueue_worker=lambda task_id, kind, execution_options=None: "job-1")


def test_process_mode_does_not_use_the_in_process_engine_and_thread_mode_does(monkeypatch):
    import delivery_api.inprocess_engine as engine_module
    used = {"engine": 0, "job": 0}
    # The test only needs the phase-mode enum, so it does not load the whole orchestrator (and the generator behind it).
    fake_orchestrator = NS(ExecutionMode=lambda value: value)
    monkeypatch.setitem(sys.modules, "delivery_api.orchestrator", fake_orchestrator)
    monkeypatch.setattr(__import__("delivery_api"), "orchestrator", fake_orchestrator, raising=False)
    class Engine:
        async def dispatch(self, *a, **k):
            used["engine"] += 1
            return True
    async def fake_execute_job(store, job_id, handler, **k):
        used["job"] += 1
        return True
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(engine_module, "_engine", Engine())
    monkeypatch.setattr(tenant_jobs, "execute_job", fake_execute_job)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    assert asyncio.run(tenant_jobs.dispatch_phase(NS(**{**_jobs_store().__dict__, **_store([]).__dict__}), _orchestrator(), "t1", "needs")) == "job-1"
    assert used == {"engine": 0, "job": 1}
    monkeypatch.setattr(config, "RUN_ISOLATION", "thread")
    asyncio.run(tenant_jobs.dispatch_phase(NS(**{**_jobs_store().__dict__, **_store([]).__dict__}), _orchestrator(), "t1", "needs"))
    assert used == {"engine": 1, "job": 1}
