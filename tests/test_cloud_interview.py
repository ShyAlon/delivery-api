"""Cloud task read shows the owner's open interview question, and finishing an interview never starts unapproved work."""
from types import SimpleNamespace as NS
import delivery_api.app as module
from vmodel_contracts.interactive import InputKind,ProcessStatus


def _pending():
    return NS(input_id="in-1",kind=InputKind.TEXT,prompt="Who is this really for?",context={},
              model_dump=lambda mode="json":{"input_id":"in-1","prompt":"Who is this really for?"})


def _store(added):
    return NS(events=lambda task_id:[],add_event=lambda *a,**k:added.append((a,k)),task=lambda task_id:{"active_phase":"intent"})


def test_cloud_read_surfaces_the_open_question_from_the_owner_session(monkeypatch):
    added=[]
    monkeypatch.setattr(module,"store",_store(added))
    monkeypatch.setattr(module,"engine_client",NS(snapshot=lambda session:NS(process_status=ProcessStatus.WAITING_INPUT,open_input=_pending())))
    snapshot,state=module._cloud_engine_read("t1",{"active_phase":"intent"},"s1")
    assert state["waiting"] is True and state["question"]=="Who is this really for?" and state["input_id"]=="in-1"
    assert state["process_live"] is True and snapshot is not None


def test_cloud_read_outside_the_interview_never_touches_the_shared_runner(monkeypatch):
    def shared(*a,**k):raise AssertionError("cloud read entered the shared local runner")
    monkeypatch.setattr(module.runner,"input_state",shared)
    monkeypatch.setattr(module,"engine_client",NS(snapshot=lambda session:NS(process_status=ProcessStatus.WAITING_INPUT,open_input=_pending())))
    snapshot,state=module._cloud_engine_read("t1",{"active_phase":"needs"},"s1")
    assert state["question"] is None and state["waiting"] is False and state["engine_process_status"]==int(ProcessStatus.WAITING_INPUT)


def test_finishing_a_cloud_interview_records_it_and_does_not_start_the_next_phase(monkeypatch):
    added=[];started=[]
    monkeypatch.setattr(module,"store",_store(added))
    monkeypatch.setattr(module,"_reconcile_terminal_http_interview",lambda task_id,snapshot:True)
    monkeypatch.setattr(module.orchestrator,"continue_after",lambda *a,**k:started.append(a))
    module._finish_cloud_interview("t1",NS(session_id="s1"))
    assert started==[] and len(added)==1
    assert added[0][1]["payload"]["started"] is False and "has not started" in added[0][0][2]


def test_a_failure_while_recording_the_finished_interview_is_an_event_not_an_error_response(monkeypatch):
    added=[]
    monkeypatch.setattr(module,"store",_store(added))
    def boom(task_id,snapshot):raise PermissionError("unknown session")
    monkeypatch.setattr(module,"_reconcile_terminal_http_interview",boom)
    module._finish_cloud_interview("t1",NS(session_id="s1"))
    assert added and added[0][0][1]=="error" and "PermissionError" in added[0][1]["payload"]["cause"]
