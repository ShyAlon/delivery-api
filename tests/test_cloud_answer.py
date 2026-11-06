"""A cloud account answers the interview through /answer; the last answer records the interview and starts nothing."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

import delivery_api.app as module
from delivery_api import config

ACTOR = NS(label="owner@example.com", id="u1", admin=False)


def _pending(input_id="in-1"):
    return NS(input_id=input_id, prompt="What does booking look like?", options=[], context={}, rationale_required=False)


def _setup(monkeypatch, *, finished, pending=None):
    seen = {"events": [], "submitted": [], "continued": []}
    task = {"task_id": "t1", "state": "running", "active_phase": "intent", "phases": []}
    store = NS(task=lambda tid: task, events=lambda tid: [], engine_session=lambda tid: "s1",
               add_event=lambda *a, **k: seen["events"].append((a, k)),
               add_review_comment=lambda *a, **k: None,
               recoverable_worker_jobs=lambda: [], decision_review=lambda *a, **k: None)
    snapshot = NS(session_id="s1", sequence=4, open_input=pending or _pending())
    def submit(session_id, response):
        seen["submitted"].append((session_id, response))
        return NS(snapshot=NS(session_id="s1", sequence=5), model_dump=lambda mode="json": {"accepted": True})
    async def quiet_watch(*a, **k):
        return None
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "runner", NS(input_state=lambda tid: {"waiting": True, "process_live": True, "input_id": "in-1"}, work_state=lambda tid: None))
    monkeypatch.setattr(module, "engine_client", NS(snapshot=lambda session_id: snapshot, submit=submit))
    monkeypatch.setattr(module, "InputResponse", lambda **kw: NS(**kw))
    monkeypatch.setattr(module, "_watch_engine_advance", quiet_watch)
    monkeypatch.setattr(module, "_reconcile_terminal_http_interview", lambda tid, snap: finished)
    async def never(tid):
        seen["continued"].append(tid)
    monkeypatch.setattr(module, "_continue_reconciled_interview", never)
    monkeypatch.setattr(module.orchestrator, "continue_after", lambda *a, **k: seen["continued"].append(a))
    return seen


def _answer(**fields):
    async def go():
        out = await module.answer_interview("t1", module.InterviewAnswer(answer="The child", **fields), ACTOR)
        await asyncio.sleep(0)
        return out
    return asyncio.run(go())


def test_a_cloud_answer_reaches_the_owner_session_and_is_recorded(monkeypatch):
    seen = _setup(monkeypatch, finished=False)
    assert _answer(decision_id="in-1") == {"status": "accepted"}
    session_id, response = seen["submitted"][0]
    assert session_id == "s1" and response.value == "The child" and response.actor_id == "owner@example.com"
    kinds = [event[0][1] for event in seen["events"]]
    assert kinds == ["engine_protocol", "answer"] and seen["events"][1][0][2] == "You: The child"
    assert seen["continued"] == []


def test_the_last_cloud_answer_records_the_interview_as_ready_and_starts_nothing(monkeypatch):
    seen = _setup(monkeypatch, finished=True)
    assert _answer(decision_id="in-1") == {"status": "accepted"}
    messages = [event[0][2] for event in seen["events"]]
    assert any("starts only when you press Start" in message for message in messages)
    finished = [event for event in seen["events"] if event[1].get("payload", {}).get("next_phase") == "needs"][0]
    assert finished[1]["payload"]["started"] is False
    assert seen["continued"] == []                        # no phase, job or provider call began


def test_an_answer_to_a_question_that_is_no_longer_open_is_refused_before_anything_is_sent(monkeypatch):
    seen = _setup(monkeypatch, finished=False, pending=_pending("in-2"))
    with pytest.raises(HTTPException) as caught:
        _answer(decision_id="in-1")
    assert caught.value.status_code == 409 and caught.value.detail["stale"] is True
    assert seen["submitted"] == [] and seen["continued"] == []
    assert seen["events"][-1][0][1] == "error"
