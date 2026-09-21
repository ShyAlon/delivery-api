from uuid import uuid4
import httpx,pytest
from vmodel_contracts.interactive import *
from delivery_api.engine_client import EngineClientError,HttpEngineClient
from delivery_api.engine_projection import UiMode,project

def request(method,url):
    req=httpx.Request(method,url);return req

def snapshot(status=ProcessStatus.RUNNING,next_step=NextStep.RUN,open_input=None):
    return EngineSnapshot(session_id='s',task_id='t',sequence=1,process_status=status,next_step=next_step,open_input=open_input)

def event(kind=EventType.RUN_STARTED,status=ProcessStatus.RUNNING,next_step=NextStep.RUN,input_request=None):
    return InteractiveEvent(event_id=uuid4(),session_id='s',task_id='t',phase_id=1,sequence=1,event_type=kind,process_status=status,next_step=next_step,input_request=input_request)

def test_start_validates_typed_response():
    body=StartSessionResponse(snapshot=snapshot(),accepted_event=event()).model_dump(mode='json')
    client=HttpEngineClient('http://engine',transport=httpx.MockTransport(lambda r:httpx.Response(201,json=body)))
    result=client.start(StartSessionRequest(task_id='t',idea='idea',model_route='m'))
    assert result.snapshot.process_status==ProcessStatus.RUNNING

def test_http_conflict_preserves_protocol_error_enum():
    error=ProtocolError(code=ProtocolErrorCode.STALE_INPUT,detail='stale').model_dump(mode='json')
    client=HttpEngineClient('http://engine',transport=httpx.MockTransport(lambda r:httpx.Response(409,json={'detail':error})))
    response=InputResponse(input_id=uuid4(),value=1,actor_id='founder')
    with pytest.raises(EngineClientError) as caught:client.submit('s',response)
    assert caught.value.http_status==409 and caught.value.error.code==ProtocolErrorCode.STALE_INPUT

def test_projection_uses_enums_not_prompt_text():
    pending=InputRequest(input_id=uuid4(),kind=InputKind.APPROVAL,prompt='arbitrary human text',options=[InputOption(value=1,label='yes')])
    a=snapshot(ProcessStatus.WAITING_INPUT,NextStep.WAIT_INPUT,pending)
    b=a.model_copy(update={'open_input':pending.model_copy(update={'prompt':'completely different'})})
    assert project(a)==project(b)==UiMode.APPROVAL_INPUT
