"""The Debug window's task diagnostics: the question, control state, engine state and last events in one document."""
from types import SimpleNamespace as NS
from enum import IntEnum
from delivery_api import task_diagnostics as td


class Status(IntEnum):
    WAITING_INPUT=30


class Kind(IntEnum):
    TEXT=1


def snapshot(open_input=True):
    pending=NS(input_id="a09c841a",kind=Kind.TEXT,prompt="Who is this really for?",options=[],rationale_required=False) if open_input else None
    return NS(session_id="s1",process_status=Status.WAITING_INPUT,next_step=NS(name="WAIT_INPUT"),sequence=7,open_input=pending)


def test_engine_summary_includes_the_open_question_text():
    out=td.engine_summary(snapshot())
    assert out["available"] and out["process_status"]==30 and out["sequence"]==7
    assert out["open_input"]["prompt"]=="Who is this really for?" and out["open_input"]["kind"]=="TEXT"


def test_engine_summary_without_open_input_or_session_or_with_error():
    assert td.engine_summary(snapshot(False))["open_input"] is None
    assert td.engine_summary(None)["available"] is False
    assert td.engine_summary(None,"the interview engine is not reachable")["error"]=="the interview engine is not reachable"


def test_build_keeps_the_last_events_in_order_with_payloads_and_counts_the_rest():
    events=[{"id":i,"kind":"progress","message":f"m{i}","payload":{"n":i},"phase_key":"intent"} for i in range(50)]
    out=td.build({"task_id":"t","title":"x","state":"running"},events,{"mode":"waiting_input","prompt":"Q?"},{},td.engine_summary(None))
    assert out["events"]["total"]==50 and out["events"]["shown"]==30
    assert [e["id"] for e in out["events"]["items"]]==list(range(20,50))
    assert out["events"]["items"][-1]["payload"]=={"n":49}


def test_question_comes_from_control_then_input_state_then_engine():
    task={"task_id":"t"}
    assert td.build(task,[],{"prompt":"from control"},{"question":"from input"},None)["question"]=="from control"
    assert td.build(task,[],{},{"question":"from input"},None)["question"]=="from input"
    assert td.build(task,[],{},{},td.engine_summary(snapshot()))["question"]=="Who is this really for?"
    assert td.build(task,[],{},{},None)["question"] is None


def test_long_event_messages_are_clipped_not_dropped():
    out=td.build({"task_id":"t"},[{"id":1,"kind":"error","message":"x"*10000}],{},{},None)
    message=out["events"]["items"][0]["message"]
    assert message.startswith("xxx") and len(message)<4200 and "more characters" in message


def test_worker_state_section_shapes_rows_and_renders_times():
    ws={"worker_jobs":[{"job_id":"j1","kind":"interview","state":"running","attempt":1,"session_id":"s1","generation":3,
                        "created_at":1761472800.0,"updated_at":1761476400.0,"execution_options":{"secret":"kept-out"}}],
        "provider_attempts":[{"attempt_id":"a1","job_id":"j1","state":"dispatched","expense_reserved":10,"retail_reserved":25,
                              "expense_actual":None,"retail_actual":None,"created_at":1761472800.0,"updated_at":1761476400.0,
                              "quote":{"kept":"out"},"fingerprint":"kept-out","usage":{"kept":"out"}}]}
    out=td.build({"task_id":"t","title":"x","state":"running"},[],{}, {}, td.engine_summary(None),worker_state=ws)
    section=out["worker_state"]
    assert section["available"] is True
    job=section["worker_jobs"][0]
    assert job["job_id"]=="j1" and job["state"]=="running" and job["session_id"]=="s1"
    assert job["created_at"]=="2025-10-26T10:00:00Z" and job["updated_at"]=="2025-10-26T11:00:00Z"
    assert "execution_options" not in job
    attempt=section["provider_attempts"][0]
    assert attempt["state"]=="dispatched" and attempt["retail_reserved"]==25
    assert "quote" not in attempt and "fingerprint" not in attempt and "usage" not in attempt


def test_worker_state_omitted_when_not_passed_and_unavailable_when_none():
    out=td.build({"task_id":"t","title":"x","state":"running"},[],{}, {}, td.engine_summary(None))
    assert "worker_state" not in out


def _llm_lines(path, rows):
    import json
    (path/"llm-calls.jsonl").write_text("\n".join(json.dumps(r) for r in rows)+"\n")


def test_model_calls_projects_each_call_and_aggregates_by_stage(tmp_path):
    rows=[
        {"event":"llm_call_start","stage":"probing","provider":"ollama","model":"phi4"},
        {"event":"llm_call_end","ts":"2026-10-27T09:00:00","stage":"probing","item":"IT-1","provider":"ollama","model":"phi4",
         "latency_s":14.2,"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15},"keep_alive":"30m","tokens_per_s":18.1},
        {"event":"llm_call_end","ts":"2026-10-27T09:05:00","stage":"probing","item":"IT-2","provider":"ollama","model":"phi4",
         "latency_s":15.8,"usage":{"input_tokens":12,"output_tokens":6,"total_tokens":18},"keep_alive":"30m","tokens_per_s":17.0},
        {"event":"llm_call_end","ts":"2026-10-27T09:40:00","stage":"categorization","provider":"ollama","model":"phi4",
         "latency_s":66.7,"usage":{"input_tokens":900,"output_tokens":400,"total_tokens":1300},"keep_alive":"30m","tokens_per_s":6.0,
         "timing":{"total_ms":66700,"load_ms":800,"prompt_eval_ms":2100,"prompt_eval_count":900,"eval_ms":11300,"eval_count":400}},
    ]
    _llm_lines(tmp_path, rows)
    out=td.model_calls(tmp_path,cloud=False)
    assert len(out["calls"])==3  # start events are not calls
    first=out["calls"][0]
    assert first=={"ts":"2026-10-27T09:00:00","stage":"probing","item":"IT-1","provider":"ollama","model":"phi4",
                   "latency_s":14.2,"keep_alive":"30m","tokens_per_s":18.1,
                   "usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}
    (agg,)= [a for a in out["by_stage"] if a["stage"]=="probing"]
    assert agg["calls"]==2 and agg["total_s"]==30.0 and agg["mean_s"]==15.0
    assert agg["input_tokens"]==22 and agg["output_tokens"]==11
    cat=[a for a in out["by_stage"] if a["stage"]=="categorization"][0]
    assert cat["load_ms"]==800 and cat["prompt_eval_ms"]==2100 and cat["eval_ms"]==11300


def test_model_calls_cloud_drops_token_counts_and_rates(tmp_path):
    rows=[{"event":"llm_call_end","ts":"t","stage":"probing","provider":"ollama","model":"phi4",
           "latency_s":14.2,"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15},
           "keep_alive":"30m","tokens_per_s":18.1,
           "timing":{"total_ms":14200,"load_ms":800,"prompt_eval_ms":2100,"prompt_eval_count":10,"eval_ms":11300,"eval_count":5}}]
    _llm_lines(tmp_path, rows)
    out=td.model_calls(tmp_path,cloud=True)
    call=out["calls"][0]
    assert "usage" not in call and "tokens_per_s" not in call
    assert call["timing"]=={"total_ms":14200,"load_ms":800,"prompt_eval_ms":2100,"eval_ms":11300}  # durations stay, counts go
    agg=out["by_stage"][0]
    assert "input_tokens" not in agg and "output_tokens" not in agg
    assert agg["calls"]==1 and agg["total_s"]==14.2


def test_model_calls_without_recording_is_empty(tmp_path):
    assert td.model_calls(tmp_path,cloud=False)=={"calls":[],"by_stage":[]}


def _case_diagnostics_route_includes_model_calls_when_session_recorded(monkeypatch,tmp_path,pg_scoped_workspace):
    import json
    from fastapi.testclient import TestClient
    from delivery_api.app import app
    from delivery_api import config
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    store=pg_scoped_workspace.store
    monkeypatch.setattr('delivery_api.app.store',store)
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    from uuid import uuid4
    task=store.create_task('demo task '+uuid4().hex)
    sid='diagnostic-'+uuid4().hex
    store.set_engine_session(task['task_id'],sid)
    artifacts=tmp_path/'artifacts'
    (artifacts/sid/'interview').mkdir(parents=True)
    (artifacts/sid/'interview'/'llm-calls.jsonl').write_text(json.dumps(
        {"event":"llm_call_end","ts":"t","stage":"probing","provider":"ollama","model":"phi4",
         "latency_s":14.2,"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15},
         "keep_alive":"30m","tokens_per_s":18.1})+"\n")
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
    reply=TestClient(app).get(f"/api/tasks/{task['task_id']}/diagnostics")
    assert reply.status_code==200,reply.text[:400]
    out=reply.json()["model_calls"]
    assert out["calls"][0]["stage"]=="probing" and out["calls"][0]["keep_alive"]=="30m"
    assert out["by_stage"]==[{"provider":"ollama","model":"phi4","stage":"probing","calls":1,
                              "total_s":14.2,"input_tokens":10,"output_tokens":5,"mean_s":14.2}]


def _case_diagnostics_route_without_session_has_no_model_calls(monkeypatch,tmp_path,pg_scoped_workspace):
    from fastapi.testclient import TestClient
    from delivery_api.app import app
    from delivery_api import config
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    store=pg_scoped_workspace.store
    monkeypatch.setattr('delivery_api.app.store',store)
    from uuid import uuid4
    task=store.create_task('demo task '+uuid4().hex)
    reply=TestClient(app).get(f"/api/tasks/{task['task_id']}/diagnostics")
    assert reply.status_code==200,reply.text[:400]
    assert "model_calls" not in reply.json()


def _check_diagnostics_route_includes_model_calls_when_session_recorded(monkeypatch,tmp_path,workspace):
    from tests.test_task_diagnostics import _case_diagnostics_route_includes_model_calls_when_session_recorded
    _case_diagnostics_route_includes_model_calls_when_session_recorded(monkeypatch,tmp_path,workspace)


def test_diagnostics_route_includes_model_calls_when_session_recorded(pg_local_compat_process):
    pg_local_compat_process(_check_diagnostics_route_includes_model_calls_when_session_recorded)


def _check_diagnostics_route_without_session_has_no_model_calls(monkeypatch,tmp_path,workspace):
    from tests.test_task_diagnostics import _case_diagnostics_route_without_session_has_no_model_calls
    _case_diagnostics_route_without_session_has_no_model_calls(monkeypatch,tmp_path,workspace)


def test_diagnostics_route_without_session_has_no_model_calls(pg_local_compat_process):
    pg_local_compat_process(_check_diagnostics_route_without_session_has_no_model_calls)
