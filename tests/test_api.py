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
