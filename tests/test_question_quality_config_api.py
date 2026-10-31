import httpx
import pytest
from fastapi.testclient import TestClient

from delivery_api import app as app_module
from delivery_api.engine_client import HttpEngineClient

DEFAULTS = {"retrieval": {"max_words": 90, "top_k": 4}, "verification": {"retry_enabled": False},
            "area_cues": {"data": ["from", "own"]}}


class FakeQualityEngine:
    """Stands in for the engine's /v1/question-quality-config routes."""
    def __init__(self):
        self.config = {k: dict(v) for k, v in DEFAULTS.items()}
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-interview-engine-token"]
        if request.url.path != "/v1/question-quality-config":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json={"config_version": 1, "config": self.config, "defaults": DEFAULTS})
        import json
        body = json.loads(request.content)
        self.seen.append(body)
        if body["config"].get("retrieval", {}).get("max_words", 0) > 300:
            return httpx.Response(422, json={"detail": "retrieval.max_words must be an integer from 20 to 300"})
        for k, v in body["config"].items():
            self.config[k] = {**self.config.get(k, {}), **v}
        return httpx.Response(200, json={"config_version": 1, "config": self.config})


def _debug_registry(monkeypatch,workspace):
 from delivery_api.operator_authorization import OperatorPrincipal
 from delivery_api.operator_reads import OperatorReadStore
 from delivery_api.tenancy import current_scope
 import psycopg
 from test_postgres_store import DSN
 scope=current_scope()
 with psycopg.connect(DSN) as conn:
  conn.execute('GRANT requi_operator_read TO delivery_app')
  conn.execute("INSERT INTO operator_principals VALUES(%s,'quality-test',ARRAY['debug.read'],true) ON CONFLICT(subject) DO UPDATE SET registry_version=EXCLUDED.registry_version,capabilities=EXCLUDED.capabilities,enabled=true",(scope.owner_id,))
 reader=OperatorReadStore(workspace.store._postgres.dsn,OperatorPrincipal(scope.owner_id,'quality-test',frozenset({'debug.read'})))
 monkeypatch.setitem(app_module.app.state._state,'operator_read_store',reader)
 # Each helper runs in its own child process but shares the disposable server.
 # The fixture principal must not survive into unrelated registry tests.
 import atexit
 def cleanup():
  with psycopg.connect(DSN) as conn:conn.execute("DELETE FROM operator_principals WHERE subject=%s AND registry_version='quality-test'",(scope.owner_id,))
 atexit.register(cleanup)


def _engine(monkeypatch):
    fake = FakeQualityEngine()
    monkeypatch.setattr(app_module, "engine_client",
                        HttpEngineClient("http://engine.test", transport=httpx.MockTransport(fake)))
    return fake


def _case_get_returns_config_and_defaults_from_the_engine(engine):
    body = TestClient(app_module.app).get("/api/question-quality-config").json()
    assert body["config"] == DEFAULTS and body["defaults"] == DEFAULTS


def _case_put_forwards_the_edit_and_returns_the_saved_config(engine):
    r = TestClient(app_module.app).put("/api/question-quality-config",
                                       json={"config": {"verification": {"retry_enabled": True}}})
    assert r.status_code == 200 and r.json()["config"]["verification"]["retry_enabled"] is True
    assert engine.seen == [{"config": {"verification": {"retry_enabled": True}}}]


def _case_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing(engine):
    r = TestClient(app_module.app).put("/api/question-quality-config",
                                       json={"config": {"retrieval": {"max_words": 9999}}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "retrieval.max_words" in detail["error"] and "Nothing was changed" in detail["next"]
    assert engine.config["retrieval"]["max_words"] == 90


def _case_unreachable_engine_is_a_502_with_a_next_step(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(app_module, "engine_client",
                        HttpEngineClient("http://engine.test", transport=httpx.MockTransport(boom)))
    c = TestClient(app_module.app)
    for r in (c.get("/api/question-quality-config"),
              c.put("/api/question-quality-config", json={"config": {}})):
        assert r.status_code == 502 and "not reachable" in r.json()["detail"]["error"]
        assert "Start the engine" in r.json()["detail"]["next"]


def _check_get_returns_config_and_defaults_from_the_engine(monkeypatch,tmp_path,workspace):
 from tests.test_question_quality_config_api import _debug_registry,_case_get_returns_config_and_defaults_from_the_engine,_engine
 _debug_registry(monkeypatch,workspace)
 _case_get_returns_config_and_defaults_from_the_engine(_engine(monkeypatch))


def test_get_returns_config_and_defaults_from_the_engine(pg_local_compat_process):
 pg_local_compat_process(_check_get_returns_config_and_defaults_from_the_engine)


def _check_put_forwards_the_edit_and_returns_the_saved_config(monkeypatch,tmp_path,workspace):
 from tests.test_question_quality_config_api import _debug_registry,_case_put_forwards_the_edit_and_returns_the_saved_config,_engine
 _debug_registry(monkeypatch,workspace)
 _case_put_forwards_the_edit_and_returns_the_saved_config(_engine(monkeypatch))


def test_put_forwards_the_edit_and_returns_the_saved_config(pg_local_compat_process):
 pg_local_compat_process(_check_put_forwards_the_edit_and_returns_the_saved_config)


def _check_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing(monkeypatch,tmp_path,workspace):
 from tests.test_question_quality_config_api import _debug_registry,_case_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing,_engine
 _debug_registry(monkeypatch,workspace)
 _case_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing(_engine(monkeypatch))


def test_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing(pg_local_compat_process):
 pg_local_compat_process(_check_engine_rejection_is_a_422_that_names_the_setting_and_changes_nothing)


def _check_unreachable_engine_is_a_502_with_a_next_step(monkeypatch,tmp_path,workspace):
 from tests.test_question_quality_config_api import _debug_registry,_case_unreachable_engine_is_a_502_with_a_next_step,_engine
 _debug_registry(monkeypatch,workspace)
 _case_unreachable_engine_is_a_502_with_a_next_step(monkeypatch)


def test_unreachable_engine_is_a_502_with_a_next_step(pg_local_compat_process):
 pg_local_compat_process(_check_unreachable_engine_is_a_502_with_a_next_step)
