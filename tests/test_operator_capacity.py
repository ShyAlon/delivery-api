"""Operator Debug capacity: the numbers come from run_slots, only an admin can read them, and none of it is a cost or vendor figure."""
import time

import pytest
from fastapi.testclient import TestClient

from delivery_api import config, run_slots


@pytest.fixture(autouse=True)
def _fresh():
    run_slots.reset()
    yield
    run_slots.reset()


def _snap(host=None, peaks=(), running=None, isolation="process"):
    return {"isolation": isolation, "max_concurrent_runs": 2, "time_limit_s": 1800,
            "running": running or [], "waiting": 0,
            "recent_peaks": [{"task_id": "t%d" % i, "peak_rss_mb": p} for i, p in enumerate(peaks)],
            "host": host or {"cpus": 4, "mem_total_mb": 8192, "mem_available_mb": 4096}}


def test_the_text_says_there_is_no_memory_figure_before_any_run_has_finished():
    text = run_slots.snapshot_text(_snap())
    assert "No run has finished" in text and "Slots 2" in text and "Time limit 30 min" in text


def test_the_text_gives_room_for_runs_from_the_biggest_peak_and_calls_it_a_guess():
    text = run_slots.snapshot_text(_snap(peaks=(300, 900)))
    assert "Biggest run seen since the server started: 900 MB" in text
    assert "Room for about 4 more runs" in text and "not a limit" in text


def test_thread_mode_is_named_so_the_slot_count_is_not_misread():
    assert "one run at a time" in run_slots.snapshot_text(_snap(isolation="thread"))


def test_unreadable_machine_memory_is_stated_not_invented():
    text = run_slots.snapshot_text(_snap(host={"cpus": 2, "mem_total_mb": None, "mem_available_mb": None}, peaks=(500,)))
    assert "not readable" in text and "Room for" not in text


def test_the_snapshot_lists_running_phases_and_keeps_only_recent_peaks(monkeypatch):
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    run_slots.running["t1"] = {"started": time.time() - 12, "peak_rss_mb": 250.0, "timed_out": False}
    for i in range(60):
        run_slots.peaks["p%d" % i] = float(i)
    snap = run_slots.snapshot()
    assert snap["running"][0]["task_id"] == "t1" and snap["running"][0]["peak_rss_mb"] == 250.0
    assert len(snap["recent_peaks"]) == 10 and snap["recent_peaks"][-1]["task_id"] == "p59"


def test_no_cost_or_vendor_wording_appears_in_the_capacity_text():
    text = run_slots.snapshot_text(_snap(peaks=(300,), running=[{"task_id": "t1", "seconds": 5, "peak_rss_mb": 300.0, "timed_out": True}])).lower()
    for word in ("expense", "vendor", "cost", "price", "$", "usd", "spend"):
        assert word not in text


@pytest.fixture
def capacity_client(pg_workspace_factory,pg_task_app):
    import jwt
    from pg_workspace import KEY
    with pg_workspace_factory() as workspace:
        claims=jwt.decode(workspace.token,options={'verify_signature':False})
        claims['email']=config.OPERATOR_EMAIL
        operator=jwt.encode(claims,KEY,algorithm='ES256',headers={'kid':'fixture'})
        with TestClient(pg_task_app(workspace)) as client:
            yield workspace,client,{'Authorization':'Bearer '+operator}


def test_only_an_admin_can_read_capacity(capacity_client):
    workspace,c,operator_headers=capacity_client
    store=workspace.store
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    assert c.get('/api/admin/operations/capacity').status_code==401
    c.headers.update(workspace.headers)
    assert c.get("/api/admin/operations/capacity").status_code == 403
    c.headers.update(operator_headers)
    r = c.get("/api/admin/operations/capacity")
    assert r.status_code == 200 and r.json()["max_concurrent_runs"] == config.MAX_CONCURRENT_RUNS and "Capacity:" in r.json()["text"]


def test_only_an_admin_can_read_run_metrics_and_the_capacity_text_carries_them(monkeypatch,tmp_path,capacity_client):
    from delivery_api import run_metrics
    monkeypatch.setenv("DELIVERY_METRICS_DB", str(tmp_path / "metrics.db"))
    run_metrics.reset()
    workspace,c,operator_headers=capacity_client
    store=workspace.store
    conn = run_metrics._open()
    with conn:
        conn.execute("INSERT INTO run_samples(ts,run_key,task_id,session_id,phase,pid,procs,rss_mb,peak_rss_mb,cpu_seconds,cpu_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), "t1:needs:1", "t1", "sess1", "needs", 1, 1, 123.0, 123.0, 1.0, 5.0))
    assert c.get("/api/admin/operations/run-metrics").status_code==401
    c.headers.update(workspace.headers)
    assert c.get("/api/admin/operations/run-metrics").status_code == 403
    assert c.get("/api/admin/operations/run-metrics?task_id=t1").status_code == 403
    c.headers.update(operator_headers)
    one = c.get("/api/admin/operations/run-metrics?task_id=t1").json()
    assert one["samples"][0]["session_id"] == "sess1" and one["samples"][0]["rss_mb"] == 123.0
    assert "Resource samples" in c.get("/api/admin/operations/capacity").json()["text"]
    run_metrics.reset()
