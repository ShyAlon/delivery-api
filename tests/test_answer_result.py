from types import SimpleNamespace
from uuid import uuid4
from delivery_api.answer_result import AnswerResult,result_frames
from vmodel_contracts.interactive import EngineSnapshot,InteractiveEvent,EventType,ProcessStatus,NextStep,InputRequest,InputKind


def sample():
    op=SimpleNamespace(operation_id='one',binding=SimpleNamespace(session_id='s',task_id='t'),operation=SimpleNamespace(expected_sequence=1))
    snapshot=EngineSnapshot(session_id='s',task_id='t',sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,
        open_input=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Large exact question '+('x'*5000)))
    accepted=InteractiveEvent(event_id=uuid4(),session_id='s',task_id='t',phase_id=17,sequence=2,event_type=EventType.INPUT_ACCEPTED,
        process_status=snapshot.process_status,next_step=snapshot.next_step)
    return op,snapshot,accepted


def test_private_result_exact_chunks_and_real_phase():
    op,snapshot,accepted=sample();receiver=AnswerResult(op,token='test-nonce')
    frames=result_frames(op,snapshot,accepted,'test-nonce');assert len(frames)>1
    for row in frames:receiver.accept(row)
    assert receiver.snapshot==snapshot and receiver.accepted_event==accepted
    assert receiver.accepted_event.phase_id==17


def test_missing_changed_foreign_and_wrong_identity_results_do_not_complete():
    op,snapshot,accepted=sample();frames=result_frames(op,snapshot,accepted,'test-nonce')
    for modified in (frames[1:], [{**frames[0],'operation_id':'foreign'},*frames[1:]], [{**frames[0],'token':'wrong'},*frames[1:]],
                     [{**frames[0],'data':'A'+frames[0]['data'][1:]},*frames[1:]]):
        receiver=AnswerResult(op,token='test-nonce')
        for row in modified:receiver.accept(row)
        assert receiver.snapshot is None
    receiver=AnswerResult(op,token='test-nonce')
    for row in result_frames(op,snapshot.model_copy(update={'task_id':'other'}),accepted,'test-nonce'):receiver.accept(row)
    assert receiver.snapshot is None
