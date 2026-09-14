import os
os.environ["DELIVERY_DB_PATH"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from delivery_api.app import app
from delivery_api import config


@pytest.fixture()
def client():
    return TestClient(app)


def test_link_lifecycle_and_one_active_run(client):
    r = client.post("/links", json={"founder_label": "tester-1"},
                    headers={"x-admin-token": config.ADMIN_TOKEN})
    assert r.status_code == 200
    token = r.json()["token"]
    r = client.post("/runs", json={"token": token, "free_text": "a web app"})
    assert r.status_code == 200 and r.json()["state"] == "in_interview"
    # one active run per link:
    r = client.post("/runs", json={"token": token, "free_text": "again"})
    assert r.status_code == 409


def test_links_are_admin_only(client):
    assert client.post("/links", json={"founder_label": "x"}).status_code == 403


def test_unknown_link_rejected(client):
    assert client.post("/runs", json={"token": "nope", "free_text": "x"}).status_code == 403


def test_budget_hard_stop(client):
    r = client.post("/links", json={"founder_label": "tester-2"},
                    headers={"x-admin-token": config.ADMIN_TOKEN})
    run_id = r.json()["run_id"]
    client.post("/runs", json={"token": r.json()["token"], "free_text": "a web app"})
    r = client.post(f"/runs/{run_id}/usage", json={"record_json": "{}", "cost_usd": 7.50})
    assert r.status_code == 402
    assert client.get(f"/runs/{run_id}").json()["state"] == "budget_stopped"


# ---- error handling ----

def test_budget_stop_message_is_actionable():
    from delivery_api.budget import BudgetExceeded

    e = BudgetExceeded("run-1", 6.01, 6.00)
    msg = str(e)
    assert "budget guard" in msg and "DELIVERY_PER_RUN_BUDGET_USD" in msg and "no further llm calls" in msg.lower()


def test_job_failure_records_human_artifact(tmp_path):
    import asyncio
    import time

    from delivery_api.errors import UserFacingError
    from delivery_api.jobs import JobQueue
    from delivery_api.store import SQLiteStore

    store = SQLiteStore(str(tmp_path / "t.db"))
    run_id = store.create_link("t")["run_id"]
    q = JobQueue(store)

    async def boom(rid: str) -> str:
        raise UserFacingError("Ollama is not running.", "ollama serve not started", "run `ollama serve`")

    async def go():
        await q.start()
        q.register("generation", boom)
        rid = run_id
        await q.submit("generation", rid)
        for _ in range(50):
            arts = store.artifacts(rid)
            if arts:
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return rid

    rid = asyncio.new_event_loop().run_until_complete(go())
    arts = store.artifacts(rid)
    assert any(a["kind"] == "generation:error" and "ollama serve" in a["ref"] for a in arts), arts
