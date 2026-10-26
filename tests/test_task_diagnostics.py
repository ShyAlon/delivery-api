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
