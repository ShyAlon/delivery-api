"""The phase after the interview starts only on the owner's click, with credits as the only gate, and shows as ready."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

import delivery_api.app as module
from delivery_api import config, phase_start
from delivery_api.control_snapshot import project

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
ACTOR = NS(label="owner@example.com")
FORBIDDEN = ("expense", "vendor", "spend allowance", "provider", "price")


def _phases(**states):
    base = {"intent": "approved", "conops": "approved", "needs": "pending", "srs": "pending"}
    base.update(states)
    return [{"phase_key": k, "state": v} for k, v in base.items()]


def _task(**over):
    return {"task_id": "t1", "state": "running", "active_phase": "conops", "updated_at": 5, "gates": [],
            "stop_requested_at": None, "model_default": "deepseek:deepseek-chat", "phases": _phases(), **over}


@pytest.fixture()
def finished(pg_scoped_workspace):
    store = pg_scoped_workspace.store
    task = store.create_task("idea", "brief")
    tid = task["task_id"]
    session="phase-start-"+pg_scoped_workspace.scope.owner_id
    assert store.begin_interview_start(tid, session, "deepseek")
    assert store.reconcile_completed_interview(tid, session, 7)
    return store, tid


def test_eligible_only_when_the_interview_is_approved_and_the_phase_is_pending():
    assert phase_start.eligible(_task())
    assert not phase_start.eligible(_task(state="failed"))
    assert not phase_start.eligible(_task(active_phase="intent"))
    assert not phase_start.eligible(_task(phases=_phases(conops="running")))
    assert not phase_start.eligible(_task(phases=_phases(needs="running")))
    assert not phase_start.eligible(_task(stop_requested_at=123.0))
    assert not phase_start.eligible(None)


def test_credit_check_uses_credits_only_and_the_floor_is_the_configured_one():
    assert config.NEXT_PHASE_MIN_CREDITS == 5.0
    low = phase_start.credit_check("4.99", 5)
    assert not low["can_start"] and "4.99 credits" in low["message"] and "at least 5" in low["message"]
    assert phase_start.credit_check("5", 5)["can_start"] and phase_start.credit_check(12.5, 5)["can_start"]
    unreadable = phase_start.credit_check(None, 5)
    assert not unreadable["can_start"] and unreadable["credits_left"] is None
    for result in (low, phase_start.credit_check(20, 5), unreadable):
        assert not any(word in result["message"].lower() for word in FORBIDDEN)


def test_one_click_claims_the_phase_once(finished):
    store, tid = finished
    task = store.task(tid)
    assert phase_start.eligible(task)
    assert store.claim_next_phase(tid, "needs", "owner") is True
    assert store.claim_next_phase(tid, "needs", "owner") is False      # a second click starts nothing
    after = store.task(tid)
    assert after["active_phase"] == "needs" and after["state"] == "running"
    assert {p["phase_key"]: p["state"] for p in after["phases"]}["needs"] == "running"
    assert not phase_start.eligible(after)
    assert any(e["kind"] == "progress" and "You started" in e["message"] for e in store.events(tid))


def test_a_task_whose_interview_is_not_done_cannot_be_claimed(pg_scoped_workspace):
    store = pg_scoped_workspace.store
    tid = store.create_task("idea", "brief")["task_id"]
    assert store.claim_next_phase(tid, "needs", "owner") is False
    assert store.claim_next_phase(tid, "srs", "owner") is False


def test_a_finished_cloud_interview_is_a_ready_state_not_an_error_and_never_a_live_wait():
    live = {"process_live": False, "waiting": False}
    ready = project(_task(), live, ready_next="needs")
    assert ready.mode == "ready" and ready.actions == ["start_next_phase"] and ready.diagnostic is None
    assert ready.owner == "delivery" and ready.precondition == {"phase": "needs"} and ready.kind == "next_phase"
    assert project(_task(), live).mode == "failed"                      # without the cloud rule it still reads as unowned work
    assert project(_task(), {"process_live": True, "waiting": False}, ready_next="needs").mode != "ready"
    gate = _task(gates=[{"state": "waiting", "after_phase": "conops", "gate_key": "g", "label": "Approve", "revision": 1}])
    assert project(gate, live, ready_next="needs").mode == "waiting_gate"


def _route(monkeypatch, *, credits="12", task=None, admitted=True, session="s-1", claimed="job-1", cloud=True):
    calls = {"prepared": [], "audits": [], "ran": []}
    store = NS(task=lambda tid: task if task is not None else _task(),
               engine_session=lambda tid: session,
               own_credit_balance=lambda rate: {"credits": credits, "usd": 1},
               audit=lambda *a, **k: calls["audits"].append(a),
               prepare_next_phase=lambda tid, phase, actor, model: calls["prepared"].append((tid, phase, actor, model)) or claimed)
    async def fake_run(task_id, phase, label, job_id):
        calls["ran"].append((task_id, phase, job_id))
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "cloud_execution_admitted", lambda: admitted)
    monkeypatch.setattr(module, "_run_next_phase", fake_run)
    monkeypatch.setattr(config, "CLOUD_TENANTS", cloud)
    return calls


def _call(confirm):
    async def go():
        out = await module.start_next_phase("t1", module.StartNextPhaseRequest(confirm=confirm), ACTOR)
        await asyncio.sleep(0)
        return out
    return asyncio.run(go())


def test_preview_spends_nothing_and_says_credits_left(monkeypatch):
    calls = _route(monkeypatch)
    out = _call(False)
    assert out["status"] == "preview" and out["can_start"] is True and out["credits_left"] == 12.0 and "12.00 credits" in out["message"]
    assert calls["prepared"] == [] and calls["ran"] == []


def test_confirm_with_enough_credits_prepares_one_job_for_the_recorded_model_and_runs_it(monkeypatch):
    calls = _route(monkeypatch)
    out = _call(True)
    assert out == {"status": "started", "phase": "needs", "job_id": "job-1"}
    assert calls["prepared"] == [("t1", "needs", "owner@example.com", "deepseek:deepseek-chat")] and calls["ran"] == [("t1", "needs", "job-1")]


def test_too_few_credits_refuses_in_credits_words_and_starts_nothing(monkeypatch):
    calls = _route(monkeypatch, credits="2.5")
    with pytest.raises(HTTPException) as caught:
        _call(True)
    detail = caught.value.detail
    assert caught.value.status_code == 409 and "2.50 credits" in detail["cause"] and "Add credits" in detail["next"]
    assert not any(word in str(detail).lower() for word in FORBIDDEN)
    assert calls["prepared"] == [] and calls["ran"] == [] and calls["audits"][0][1] == "phase.start_refused"


@pytest.mark.parametrize("kwargs,status", [({"admitted": False}, 503), ({"session": None}, 409),
                                            ({"task": _task(state="failed")}, 409), ({"claimed": None}, 409),
                                            ({"task": _task(model_default="openai:unmanaged")}, 409)])
def test_every_other_refusal_starts_nothing(monkeypatch, kwargs, status):
    calls = _route(monkeypatch, **kwargs)
    with pytest.raises(HTTPException) as caught:
        _call(True)
    assert caught.value.status_code == status and calls["ran"] == []


def test_a_phase_that_cannot_launch_fails_with_a_plain_message(monkeypatch):
    events = []
    store = NS(update_phase=lambda tid, phase, state, **k: events.append(("phase", state, k.get("detail"))),
               add_event=lambda *a, **k: events.append(("event", a[1], a[2])))
    monkeypatch.setattr(module, "store", store)
    import delivery_api.tenant_jobs as jobs
    async def boom(*a, **k):
        raise RuntimeError("owner spend allowance exhausted")
    monkeypatch.setattr(jobs, "dispatch_phase", boom)
    asyncio.run(module._run_next_phase("t1", "needs", "Stakeholder needs", "job-1"))
    assert events[0][:2] == ("phase", "failed") and events[1][:2] == ("event", "error")
    assert not any("spend allowance" in str(item).lower() for item in events)


def test_the_page_shows_a_ready_panel_with_a_styled_button_and_tooltip_and_binds_it():
    modes = (WEB / "ui-modes.json").read_text()
    assert '"ready_next"' in modes and '"next_phase"' in modes
    state = (WEB / "ui-state.js").read_text()
    assert "control.mode==='ready'?'ready_next'" in state and "show_next_phase" in state
    progress = (WEB / "progress.js").read_text()
    assert 'id="startnext"' in progress and "title=" in progress and "next-phase-button" in progress
    task = (WEB / "task.js").read_text()
    assert "nextPhasePanel(" in task and "#startnext" in task and "showNextPhase" in task
    assert task.count("view.mode==='ready_next'") == 2                    # the timer stops counting while nothing runs
    actions = (WEB / "taskactions.js").read_text()
    assert "/start-next-phase" in actions and "confirm:false" in actions and "confirm:true" in actions and "showNextPhase" in actions.split("return {")[1]
    assert re.search(r"\.next-phase-panel\{", (WEB / "style.css").read_text())


def test_ollama_pin_starts_only_when_the_local_route_is_managed(monkeypatch):
    monkeypatch.setenv("DELIVERY_LOCAL_PREVIEW", "1")
    calls = _route(monkeypatch, task=_task(model_default="ollama:qwen2.5-coder:14b"))
    out = _call(True)
    assert out["status"] == "started"
    assert calls["prepared"] == [("t1", "needs", "owner@example.com", "ollama:qwen2.5-coder:14b")]


@pytest.mark.parametrize('flag',[None,'0','1'])
def test_supported_ollama_route_does_not_depend_on_preview_flag(monkeypatch,flag):
    if flag is None:monkeypatch.delenv('DELIVERY_LOCAL_PREVIEW',raising=False)
    else:monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW',flag)
    calls=_route(monkeypatch,task=_task(model_default='ollama:qwen2.5-coder:14b'))
    assert _call(True)['status']=='started'
    assert calls['prepared']==[('t1','needs','owner@example.com','ollama:qwen2.5-coder:14b')]


def test_unknown_or_malformed_pins_are_denied(monkeypatch):
    monkeypatch.delenv("DELIVERY_LOCAL_PREVIEW", raising=False)
    for bad in ("openai:gpt-5", "ollama", "ollama:", "", ":qwen2.5-coder:14b"):
        _route(monkeypatch, task=_task(model_default=bad))
        with pytest.raises(HTTPException) as caught:
            _call(True)
        assert caught.value.status_code == 409 and "no recorded model" in caught.value.detail["cause"], bad


def test_finished_fixture_uses_restricted_pg_and_forbids_sqlite(finished):
    from delivery_api.postgres_store import PostgresStore
    from pg_storage_guard import assert_pg_storage_only
    store,tid=finished
    assert isinstance(store,PostgresStore)
    with store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'
    assert_pg_storage_only()


def test_retired_mode_flag_cannot_disable_admitted_start(monkeypatch):
    calls=_route(monkeypatch,cloud=False)
    assert _call(True)['status']=='started' and len(calls['ran'])==1
