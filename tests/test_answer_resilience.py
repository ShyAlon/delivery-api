"""A transient interruption mid-answer must recover: the same operation resumes,
a lost-response retry is accepted once, and the progress watcher survives."""
import asyncio
from types import SimpleNamespace as NS

import psycopg
import pytest
from fastapi import HTTPException

import delivery_api.app as module
from test_cloud_answer import ACTOR, _answer, _pending, _setup


def test_watcher_survives_a_transient_db_error(monkeypatch):
    calls = {"n": 0}
    async def drain(task_id, session_id, *, allow_absent=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
    monkeypatch.setattr(module, "_drain_engine_progress", drain)
    async def go():
        stop = asyncio.Event()
        watch = asyncio.create_task(module._watch_engine_advance("t1", "s1", stop))
        await asyncio.sleep(2.4)
        stop.set()
        await watch
    asyncio.run(go())
    assert calls["n"] >= 2, "the progress watcher died on a transient database error"


def test_a_duplicate_answer_after_a_lost_response_is_accepted_once(monkeypatch):
    seen = _setup(monkeypatch, finished=False)
    snapshot = module.engine_client.snapshot("s1")
    snapshot.open_input = None
    module.store.events = lambda tid: [
        {"id": 41, "kind": "answer", "phase_key": "intent", "payload": {"decision_id": "in-1"}}]
    assert _answer(decision_id="in-1") == {"status": "accepted"}
    assert seen["submitted"] == [], "an already-consumed input must not be submitted twice"


def test_an_unrelated_open_input_is_not_mistaken_for_the_duplicate(monkeypatch):
    seen = _setup(monkeypatch, finished=False)
    snapshot = module.engine_client.snapshot("s1")
    snapshot.open_input = None
    module.store.events = lambda tid: [
        {"id": 41, "kind": "answer", "phase_key": "intent", "payload": {"decision_id": "in-9"}}]
    with pytest.raises(HTTPException) as caught:
        _answer(decision_id="in-1")
    assert caught.value.status_code == 409
    assert seen["submitted"] == []


def _resume_setup(monkeypatch, resume):
    seen = _setup(monkeypatch, finished=False)
    def submit(session_id, response):
        seen["submitted"].append((session_id, response))
        raise RuntimeError("child exact compute evidence incomplete")
    module.engine_client.submit = submit
    module.engine_client.resume = resume
    module.store.recoverable_worker_jobs = lambda: [
        {"job_id": "job-1", "task_id": "t1", "session_id": "s1", "state": "paused"}]
    module.store.decision_review = lambda tid, key, kind: {
        "operation": {"kind": "interview.answer", "wait_id": "in-1"}}
    return seen


def test_a_transient_child_death_resumes_the_same_operation(monkeypatch):
    resumed = []
    def resume(job_id):
        resumed.append(job_id)
        return NS(snapshot=NS(session_id="s1", sequence=5))
    seen = _resume_setup(monkeypatch, resume)
    assert _answer(decision_id="in-1") == {"status": "accepted"}
    assert resumed == ["job-1"], "recovery must resume the interrupted job, never a new operation"
    kinds = [event[0][1] for event in seen["events"]]
    assert "error" not in kinds and kinds[-1] == "answer"


def test_a_live_prior_lease_is_waited_out_before_resuming(monkeypatch):
    resumed = []
    slept = []
    def resume(job_id):
        resumed.append(job_id)
        if len(resumed) == 1:
            raise PermissionError("prior child lease still live; wait for expiry")
        return NS(snapshot=NS(session_id="s1", sequence=5))
    seen = _resume_setup(monkeypatch, resume)
    original = asyncio.sleep
    async def quick(seconds):
        slept.append(seconds)
        await original(0)
    monkeypatch.setattr(asyncio, "sleep", quick)
    assert _answer(decision_id="in-1") == {"status": "accepted"}
    assert resumed == ["job-1", "job-1"] and slept, "the prior lease must be waited out, not raced"


def test_a_resume_that_keeps_dying_surfaces_the_original_failure(monkeypatch):
    def resume(job_id):
        raise RuntimeError("child exact compute evidence incomplete")
    seen = _resume_setup(monkeypatch, resume)
    original = asyncio.sleep
    async def quick(seconds):
        await original(0)
    monkeypatch.setattr(asyncio, "sleep", quick)
    with pytest.raises(HTTPException) as caught:
        _answer(decision_id="in-1")
    assert caught.value.status_code == 409 and caught.value.detail["stale"] is True
