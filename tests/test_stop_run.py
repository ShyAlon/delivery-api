"""A user stops their own running phase; an admin can stop any. The stop ends the child process, tells the owner, and is audited."""
import asyncio
import os
import sys
import time
from types import SimpleNamespace as NS

import pytest

from delivery_api import config, run_slots


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    run_slots.reset()
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 1800)
    yield
    run_slots.reset()


def _store(events, audits, fail_audit=False):
    def audit(*args):
        if fail_audit:
            raise RuntimeError("audit store down")
        audits.append(args)
    return NS(add_event=lambda *a, **k: events.append((a, k)), audit=audit, task=lambda task_id: {"active_phase": "needs"})


def _run_and_stop(by, label, events, audits, fail_audit=False):
    procs = {}
    async def go():
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True)
        procs["t1"] = child
        async def work():
            await child.wait()
            return True
        async def stopper():
            while not run_slots.is_running("t1"):
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.1)
            assert run_slots.request_stop("t1", by, label) is True
        orchestrator = NS(runner=NS(processes=procs))
        started = time.time()
        await asyncio.gather(run_slots.run_limited(_store(events, audits, fail_audit), orchestrator, "t1", work, tick_s=0.05), stopper())
        return child.returncode, time.time() - started
    return asyncio.run(go())


@pytest.mark.skipif(os.name != "posix", reason="process groups need posix")
def test_an_owner_stop_ends_the_child_tells_them_and_is_audited():
    events, audits = [], []
    code, seconds = _run_and_stop("owner", "owner@example.com", events, audits)
    assert code is not None and code != 0 and seconds < 20
    assert any("You stopped this run. You can start it again." in e[0][2] for e in events)
    assert not any("time limit" in e[0][2] for e in events)
    assert audits and audits[0][:3] == ("owner@example.com", "run.stopped", "t1") and audits[0][3]["by"] == "owner"
    assert run_slots.running == {}


@pytest.mark.skipif(os.name != "posix", reason="process groups need posix")
def test_an_operator_stop_says_so_to_the_owner():
    events, audits = [], []
    _run_and_stop("operator", "shy.alon@gmail.com", events, audits)
    assert any("An operator stopped this run. You can start it again." in e[0][2] for e in events)
    assert audits[0][0] == "shy.alon@gmail.com" and audits[0][3]["by"] == "operator"


@pytest.mark.skipif(os.name != "posix", reason="process groups need posix")
def test_a_failing_audit_write_does_not_hold_the_slot():
    events, audits = [], []
    _run_and_stop("owner", "owner@example.com", events, audits, fail_audit=True)
    assert run_slots.running == {} and audits == []
    assert any("You stopped this run" in e[0][2] for e in events)


def test_nothing_running_means_nothing_to_stop_and_a_second_request_is_harmless():
    assert run_slots.is_running("t1") is False and run_slots.request_stop("t1", "owner", "x") is False
    run_slots.running["t1"] = {"started": time.time(), "peak_rss_mb": 0.0, "timed_out": False}
    assert run_slots.request_stop("t1", "owner", "first") is True and run_slots.request_stop("t1", "operator", "second") is True
    assert run_slots.running["t1"]["stop_by"] == "owner" and run_slots.running["t1"]["stop_label"] == "first"


# The routes, called directly with a fake store (the same way test_cloud_answer drives the answer route).
import delivery_api.app as module
from fastapi import HTTPException

ACTOR = NS(label="owner@example.com", id="u1", admin=False)


def _route_setup(monkeypatch, *, isolation="process", owned=True, cloud=True):
    audits = []
    store = NS(task=lambda task_id: {"task_id": task_id} if owned else None, audit=lambda *a: audits.append(a))
    monkeypatch.setattr(config, "CLOUD_TENANTS", cloud)
    monkeypatch.setattr(config, "RUN_ISOLATION", isolation)
    monkeypatch.setattr(module, "store", store)
    return audits


def _stop(confirm):
    return module.stop_run("t1", module.StopRunRequest(confirm=confirm), ACTOR)


def test_the_preview_says_what_stopping_does_and_changes_nothing(monkeypatch):
    audits = _route_setup(monkeypatch)
    run_slots.running["t1"] = {"started": time.time(), "peak_rss_mb": 0.0, "timed_out": False}
    out = _stop(False)
    assert out["status"] == "preview" and out["running"] is True and "may still be counted" in out["message"]
    assert "stop_by" not in run_slots.running["t1"] and audits == []


def test_confirming_stops_the_owners_run_and_records_the_request(monkeypatch):
    audits = _route_setup(monkeypatch)
    run_slots.running["t1"] = {"started": time.time(), "peak_rss_mb": 0.0, "timed_out": False}
    assert _stop(True)["status"] == "stopping"
    assert run_slots.running["t1"]["stop_by"] == "owner" and audits == [("owner@example.com", "run.stop_requested", "t1", {"by": "owner"})]


def test_the_stop_is_refused_when_nothing_is_running_or_the_task_is_not_theirs_or_runs_are_threads(monkeypatch):
    _route_setup(monkeypatch)
    with pytest.raises(HTTPException) as nothing:
        _stop(True)
    assert nothing.value.status_code == 409
    _route_setup(monkeypatch, owned=False)
    with pytest.raises(HTTPException) as foreign:
        _stop(True)
    assert foreign.value.status_code == 404
    _route_setup(monkeypatch, isolation="thread")
    with pytest.raises(HTTPException) as threads:
        _stop(True)
    assert threads.value.status_code == 409
    _route_setup(monkeypatch, cloud=False)
    with pytest.raises(HTTPException) as local:
        _stop(True)
    assert local.value.status_code == 409


def test_the_task_view_offers_stop_only_for_a_running_phase_in_process_mode(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    assert module._run_control("t1") == {"stoppable": False}
    run_slots.running["t1"] = {"started": time.time(), "peak_rss_mb": 0.0, "timed_out": False}
    assert module._run_control("t1") == {"stoppable": True}
    monkeypatch.setattr(config, "RUN_ISOLATION", "thread")
    assert module._run_control("t1") == {"stoppable": False}
