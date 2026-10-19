"""An admin stops any running phase from the capacity view; the owner is told it was an operator; a normal user cannot."""
import time
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import delivery_api.app as module
from delivery_api import auth, config, run_slots
from delivery_api.app import app
from delivery_api.store import SQLiteStore

ADMIN = NS(label="shy.alon@gmail.com", id="a1", admin=True)


@pytest.fixture(autouse=True)
def _fresh():
    run_slots.reset()
    yield
    run_slots.reset()


def _running(task_id="t9"):
    run_slots.running[task_id] = {"started": time.time(), "peak_rss_mb": 12.0, "timed_out": False}


def _stop(confirm, task_id="t9"):
    return module.admin_stop_run(module.AdminStopRunRequest(task_id=task_id, confirm=confirm), ADMIN)


def test_the_preview_names_the_task_and_changes_nothing(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    _running()
    out = _stop(False)
    assert out["status"] == "preview" and out["running"] is True and "t9" in out["message"] and "operator" in out["message"]
    assert "stop_by" not in run_slots.running["t9"]


def test_confirming_marks_any_owners_run_as_stopped_by_the_operator(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    _running()
    assert _stop(True)["status"] == "stopping"
    info = run_slots.running["t9"]
    assert info["stop_by"] == "operator" and info["stop_label"] == "shy.alon@gmail.com"
    assert run_slots.snapshot()["running"][0]["stopping"] is True
    assert "being stopped" in run_slots.snapshot_text(run_slots.snapshot())


def test_refused_when_nothing_runs_for_that_task_or_runs_are_threads_or_local(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    with pytest.raises(HTTPException) as idle:
        _stop(True, "nobody")
    assert idle.value.status_code == 409
    monkeypatch.setattr(config, "RUN_ISOLATION", "thread")
    _running()
    with pytest.raises(HTTPException) as threads:
        _stop(True)
    assert threads.value.status_code == 409 and "stop_by" not in run_slots.running["t9"]
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    with pytest.raises(HTTPException) as local:
        _stop(True)
    assert local.value.status_code == 404


def test_a_normal_user_cannot_stop_anyones_run(monkeypatch, tmp_path):
    store = SQLiteStore(str(tmp_path / "stop.db")); app.state.store = store
    monkeypatch.setattr("delivery_api.app.store", store)
    monkeypatch.setattr(config, "SESSION_SECRET", "test-secret")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "google-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "google-secret")
    auth.limiter = auth.RateLimiter()
    user = store.upsert_user("github", "9", "u@example.com", "u", 100, False)
    store.create_session("usersid", user["user_id"], time.time() + 1000)
    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE, "usersid")
    _running()
    r = client.post("/api/admin/operations/stop-run", json={"task_id": "t9", "confirm": True}, headers={"origin": "http://testserver"})
    assert r.status_code == 403 and "stop_by" not in run_slots.running["t9"]
