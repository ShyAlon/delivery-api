"""The approved minimum activity markers: each write/refusal path leaves exactly the trail the
observability review promised. Hermetic: stub stores and engines, markers collected from the
delivery_api.activity logger."""
import asyncio
import logging
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

import delivery_api.app as module
from delivery_api import config

ACTOR = NS(label="user@example.com", id="u1", admin=True)


@pytest.fixture
def marks():
    logger = logging.getLogger("delivery_api.activity")
    lines = []
    class H(logging.Handler):
        def emit(self, record): lines.append(record.getMessage())
    h = H(); logger.addHandler(h)
    yield lines
    logger.removeHandler(h)

def _kinds(lines):
    return [line.split("|")[1].strip() for line in lines]


def test_delete_marks_success_and_refusals(marks, monkeypatch):
    monkeypatch.setattr(module, "store", NS(delete_task=lambda *a, **k: {}))
    assert module.delete_task("t1", module.DeleteTaskRequest(confirm=True), ACTOR)["status"] == "deleted"
    assert "task.deleted" in _kinds(marks)
    monkeypatch.setattr(module, "store", NS(delete_task=lambda *a, **k: (_ for _ in ()).throw(KeyError())))
    with pytest.raises(HTTPException):
        module.delete_task("t1", module.DeleteTaskRequest(confirm=True), ACTOR)
    monkeypatch.setattr(module, "store", NS(delete_task=lambda *a, **k: (_ for _ in ()).throw(ValueError("active"))))
    with pytest.raises(HTTPException):
        module.delete_task("t1", module.DeleteTaskRequest(confirm=True), ACTOR)
    assert _kinds(marks).count("task.delete.refused") == 2


def _stop_env(monkeypatch, outcome):
    import delivery_api.cooperative_stop as cs
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"task_id": tid, "state": "stop_requested"}))
    monkeypatch.setattr(module, "cloud_execution_admitted", lambda: True)
    monkeypatch.setattr(cs, "request_stop", outcome)


def test_cooperative_stop_marks_request_result_and_refusal(marks, monkeypatch):
    _stop_env(monkeypatch, lambda store, engine, tid, **k: {"status": "stop_requested"})
    assert module.stop_task("t1", ACTOR)["status"] == "stop_requested"
    assert "task.stop.requested" in _kinds(marks) and "task.stop.result" in _kinds(marks)
    from delivery_api.attempt_ledger import AdmissionDenied
    _stop_env(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(AdmissionDenied("no")))
    with pytest.raises(HTTPException):
        module.stop_task("t1", ACTOR)
    assert "task.stop.refused" in _kinds(marks)


def test_reconcile_marks_request_and_result(marks, monkeypatch):
    _stop_env(monkeypatch, lambda store, engine, tid, **k: {"status": "stop_requested"})
    assert module.reconcile_task_stop("t1", ACTOR)["status"] == "stop_requested"
    reqs = [l for l in marks if "task.stop.requested" in l and "reconcile=" in l]
    assert reqs and "task.stop.result" in _kinds(marks)


def test_stop_run_refusal_is_marked(marks, monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"task_id": tid}))
    from delivery_api import run_slots
    monkeypatch.setattr(run_slots, "request_stop", lambda *a: False)
    with pytest.raises(HTTPException):
        module.stop_run("t1", module.StopRunRequest(confirm=True), ACTOR)
    assert "run.stop.refused" in _kinds(marks)


def test_artifact_download_marks_success_and_refusal(marks, monkeypatch, tmp_path):
    f = tmp_path / "out.json"; f.write_text("{}")
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    monkeypatch.setattr(module,"store",NS(task=lambda tid:{"task_id":tid,"is_test":False},read_artifact_bytes=lambda *a:b"{}"))
    assert module.task_artifact_download("t1", "needs", "out.json")
    assert "task.export" in _kinds(marks)
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "ARTIFACT_MANIFEST_READS", True,raising=False)
    monkeypatch.setattr(module, "store", NS(task=lambda tid:{"task_id":tid,"is_test":False},_postgres=True,read_artifact_bytes=lambda *a: (_ for _ in ()).throw(KeyError())))
    with pytest.raises(HTTPException):
        module.task_artifact_download("t1", "needs", "out.json")
    assert "task.export.refused" in _kinds(marks)


def test_artifacts_zip_refusal_is_marked(marks, monkeypatch):
    monkeypatch.setattr(module,"store",NS(task=lambda tid:{"task_id":tid,"is_test":False}))
    monkeypatch.delenv("DELIVERY_LOCAL_PREVIEW", raising=False)
    monkeypatch.setattr(module, "_task_artifact_snapshot", lambda snapshot_task_id, **kwargs: [])
    with pytest.raises(HTTPException):
        module.task_artifacts_zip("t1")
    assert "task.export.refused" in _kinds(marks)


def test_debug_zip_marks_download(marks, monkeypatch, tmp_path):
    import delivery_api.debug_export as de
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"generation": 1,"state":"done"}, events=lambda tid: []))
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    monkeypatch.setattr(module, "_recorded_engine_session", lambda tid: "sess1")
    monkeypatch.setattr(module, "authorized_session", lambda *a: tmp_path)
    monkeypatch.setattr(module, "get_task", lambda tid: {"control_snapshot": {}})
    # Marker unit contract only; registry/session authorization has PG route tests.
    monkeypatch.setattr(module.app.state,"operator_read_store",NS(authorize_debug=lambda store: {}),raising=False)
    monkeypatch.setattr(de, "diagnostic_zip", lambda *a, **k: b"PK")
    out = module.download_debug_zip("t1", module.BrowserDiagnostics(), ACTOR)
    assert out.media_type == "application/zip" and "task.export" in _kinds(marks)


def test_package_download_refusal_is_marked(marks, monkeypatch):
    monkeypatch.setattr(module, "store", NS(task=lambda tid: None))
    with pytest.raises(HTTPException):
        module.download_final_package("t1", "a" * 64, 1, ACTOR)
    assert "task.export.refused" in _kinds(marks)


def test_retired_host_settings_cannot_record_saved_markers(marks,monkeypatch):
    assert not hasattr(module,'set_artifacts_root')
    assert not hasattr(module,'set_engine_python')
    assert 'settings.saved' not in _kinds(marks)


def test_question_quality_config_marks_saved_and_refused(marks, monkeypatch):
    from delivery_api.engine_client import QualityConfigRejected
    monkeypatch.setattr(module, "engine_client", NS(save_question_quality_config=lambda c: {"ok": 1}))
    assert module.put_question_quality_config(module.QualityConfigRequest(config={}), ACTOR) == {"ok": 1}
    monkeypatch.setattr(module, "engine_client", NS(save_question_quality_config=lambda c: (_ for _ in ()).throw(QualityConfigRejected("bad"))))
    with pytest.raises(HTTPException):
        module.put_question_quality_config(module.QualityConfigRequest(config={}), ACTOR)
    assert "settings.saved" in _kinds(marks) and "settings.save_refused" in _kinds(marks)


def test_start_next_phase_marks_credit_refusal_and_queue(marks, monkeypatch):
    import delivery_api.phase_start as ps
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(module, "cloud_execution_admitted", lambda: True)
    monkeypatch.setattr(ps, "eligible", lambda task: True)
    monkeypatch.setattr(module, "local_route", NS(managed_routes=lambda: ["ollama"]))
    monkeypatch.setattr(ps, "credit_check", lambda credits, minimum: {"can_start": False, "message": "not enough"})
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"task_id": tid, "model_default": "ollama:q"},
                                          engine_session=lambda tid: "s1",
                                          own_credit_balance=lambda r: {"credits": 0},
                                          audit=lambda *a, **k: None))
    with pytest.raises(HTTPException):
        asyncio.run(module.start_next_phase("t1", module.StartNextPhaseRequest(confirm=True), ACTOR))
    assert "phase.start.credit_refused" in _kinds(marks)

    monkeypatch.setattr(ps, "credit_check", lambda credits, minimum: {"can_start": True, "message": "ok"})
    async def _noop(*a): return None
    monkeypatch.setattr(module, "_run_next_phase", _noop)
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"task_id": tid, "model_default": "ollama:q"},
                                          engine_session=lambda tid: "s1",
                                          own_credit_balance=lambda r: {"credits": 10},
                                          audit=lambda *a, **k: None,
                                          prepare_next_phase=lambda *a: "job1"))
    out = asyncio.run(module.start_next_phase("t1", module.StartNextPhaseRequest(confirm=True), ACTOR))
    assert out["status"] == "started" and "phase.start.queued" in _kinds(marks)


def test_rerun_marks_success_and_refusal(marks, monkeypatch):
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    task = {"task_id": "t1", "phases": [{"phase_key": "needs", "position": 1}]}
    monkeypatch.setattr(module, "store", NS(task=lambda tid: task,
                                          rerun_prerequisite=lambda *a: (_ for _ in ()).throw(KeyError())))
    with pytest.raises(HTTPException):
        module.rerun("t1", module.RerunRequest(from_phase="needs", confirm=True), None, ACTOR)
    assert "task.rerun.refused" in _kinds(marks)
    monkeypatch.setattr(module, "store", NS(task=lambda tid: task,
                                          rerun_prerequisite=lambda *a: None,
                                          prepare_rerun=lambda *a: 2,
                                          add_event=lambda *a, **k: None))
    assert module.rerun("t1", module.RerunRequest(from_phase="needs", confirm=True), None, ACTOR)["generation"] == 2
    assert "task.rerun" in _kinds(marks)


def test_stream_close_is_marked(marks, monkeypatch):
    monkeypatch.setattr(module, "store", NS(task=lambda tid: {"task_id": tid}, events=lambda tid, *a: []))
    async def go():
        request = NS(is_disconnected=lambda: asyncio.sleep(0, result=False))
        resp = await module.task_stream("t1", request)
        gen = resp.body_iterator
        await gen.__anext__()
        await gen.aclose()
    asyncio.run(go())
    assert "stream.disconnect" in _kinds(marks)


def test_dispatch_rejection_and_claim_are_marked(marks, monkeypatch):
    import delivery_api.tenant_jobs as tj
    from delivery_api.tenancy import TenantScope, tenant_context
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    scope = TenantScope("00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000a")
    store = NS(worker_job=lambda jid: None)
    with tenant_context(scope):
        with pytest.raises(PermissionError):
            asyncio.run(tj.dispatch_phase(store, None, "t1", "needs", queued_job_id="j1"))
    assert "phase.dispatch.rejected" in _kinds(marks)

    job = {"task_id": "t1", "kind": "phase:needs", "state": "pending", "execution_options": {}}
    store = NS(worker_job=lambda jid: job)
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    async def _done(store, job_id, run): return True
    monkeypatch.setattr(tj, "execute_job", _done)
    with tenant_context(scope):
        out = asyncio.run(tj.dispatch_phase(store, None, "t1", "needs", queued_job_id="j1"))
    assert out == "j1" and "phase.dispatch.claimed" in _kinds(marks)


def test_execute_job_marks_a_failure_record_that_itself_fails(marks, monkeypatch):
    import delivery_api.tenant_jobs as tj
    from delivery_api.tenancy import TenantScope, tenant_context
    capability = NS(task_id="t1", session_id="s1", validate=lambda store: None)
    store = NS(claim_worker=lambda jid: capability,
               task=lambda tid: {"task_id": tid},
               finish_worker=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    async def handler(cap): raise RuntimeError("the work failed")
    with tenant_context(TenantScope("00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000a")):
        with pytest.raises(RuntimeError, match="^the work failed$"):
            asyncio.run(tj.execute_job(store, "j1", handler))
    assert "job.failure_record_failed" in _kinds(marks)


def test_a_held_prepaid_settlement_is_marked(marks, monkeypatch):
    from delivery_api import prepaid
    from delivery_api.attempt_ledger import AdmissionDenied
    from delivery_api.tenancy import TenantScope, tenant_context
    charge = {"attempt_id": "a1", "state": "pending", "units_per_credit": 5, "charged_units": None}
    calls = []
    def execute_native(sql, params=None, *, write=False):
        calls.append((sql, params, write))
        assert write is False, "held settlement must not mutate money"
        if len(calls) == 1:
            assert sql == "SELECT pg_advisory_xact_lock(%s)"
            assert len(params) == 1 and isinstance(params[0], int)
            return NS(fetchone=lambda: None)
        if len(calls) == 2:
            assert sql == "SELECT * FROM credit_charges WHERE attempt_id=%s FOR UPDATE"
            assert params == ("a1",)
            return NS(fetchone=lambda: charge)
        assert len(calls) == 3
        assert sql == "SELECT * FROM credit_accounts WHERE owner_id=%s FOR UPDATE"
        assert params == ("00000000-0000-0000-0000-00000000000a",)
        return NS(fetchone=lambda: None)
    store = NS(_execute_native=execute_native)
    with tenant_context(TenantScope("00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000a")):
        with pytest.raises(AdmissionDenied, match="^prepaid account disappeared; settlement held$"):
            prepaid.debit(store, {"attempt_id": "a1", "quote": {}}, 1)
    assert len(calls) == 3
    assert charge["state"] == "pending" and charge["charged_units"] is None
    assert "credits.settlement_held" in _kinds(marks)
