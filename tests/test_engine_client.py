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


def test_client_sends_service_token_header():
 seen=[]
 body=StartSessionResponse(snapshot=snapshot(),accepted_event=event()).model_dump(mode='json')
 client=HttpEngineClient('http://engine',token='shared-secret',transport=httpx.MockTransport(lambda r:(seen.append(r.headers['X-Interview-Engine-Token']) or httpx.Response(201,json=body))))
 client.start(StartSessionRequest(task_id='t',idea='idea',model_route='m'))
 assert seen==['shared-secret']


def test_client_does_not_preempt_long_running_model_request():
 client=HttpEngineClient('http://engine')
 assert client._http.timeout.read is None


def test_route_preview_passes_engine_model_map_and_preserves_conflict():
 map={'probing':['ollama','phi4']}
 client=HttpEngineClient('http://engine',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'route':'ollama','model_map':map})))
 assert client.preview_route('ollama')['model_map']==map
 error=ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail="selected route conflicts with INTERVIEW_ENGINE_MODEL_PROBING=ollama:phi4").model_dump(mode='json')
 client=HttpEngineClient('http://engine',transport=httpx.MockTransport(lambda r:httpx.Response(422,json={'detail':error})))
 with pytest.raises(EngineClientError,match='INTERVIEW_ENGINE_MODEL_PROBING'):
  client.preview_route('deepseek')


def test_cloud_engine_calls_carry_verified_scope_not_caller_owner(monkeypatch):
    from delivery_api import config
    from delivery_api.tenancy import TenantScope,tenant_context,TenantAccessDenied
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'route':'local','model':'fake'})
    client=HttpEngineClient('http://engine',token='test-service',transport=httpx.MockTransport(handler))
    with pytest.raises(TenantAccessDenied):client.default_route()
    owner='00000000-0000-0000-0000-000000000001'
    with tenant_context(TenantScope(owner)):client.default_route()
    assert len(seen)==1
    assert seen[0].headers['X-Requi-Owner']==owner
    assert seen[0].headers['X-Interview-Engine-Token']=='test-service'


def test_stop_client_validates_stopped_contract_and_uuid_path():
 sid=str(uuid4());seen=[]
 body=EngineSnapshot(session_id=sid,task_id='fake',sequence=1,process_status=ProcessStatus.STOP_REQUESTED,next_step=NextStep.STOP).model_dump(mode='json')
 client=HttpEngineClient('http://fake',transport=httpx.MockTransport(lambda r:seen.append(r) or httpx.Response(200,json=body)))
 assert client.stop(sid).process_status==ProcessStatus.STOP_REQUESTED
 assert seen[0].method=='POST' and seen[0].url.path==f'/v1/sessions/{sid}/stop'
 with pytest.raises(ValueError):client.stop('../other')
 assert len(seen)==1


def test_stop_client_uses_current_owner_scope(monkeypatch):
 from delivery_api import config
 from delivery_api.tenancy import TenantScope,tenant_context
 monkeypatch.setattr(config,'CLOUD_TENANTS',True)
 sid=str(uuid4());owner=str(uuid4());seen=[]
 body=EngineSnapshot(session_id=sid,task_id='fake',sequence=1,process_status=ProcessStatus.STOPPED,next_step=NextStep.STOP).model_dump(mode='json')
 client=HttpEngineClient('http://fake',transport=httpx.MockTransport(lambda r:seen.append(r) or httpx.Response(200,json=body)))
 with tenant_context(TenantScope(owner)):
  assert client.stop(sid).process_status==ProcessStatus.STOPPED
 assert seen[0].headers['X-Requi-Owner']==owner

def _diag_response(status, block=None, raw_header=None):
    import base64, json as _json
    headers = {}
    if raw_header is not None:
        headers['x-engine-operator-diagnostics'] = raw_header
    elif block is not None:
        headers['x-engine-operator-diagnostics'] = base64.urlsafe_b64encode(
            _json.dumps(block).encode()).decode('ascii')
    error = ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR, detail='engine program failed during start: X', retryable=True)
    return httpx.Response(status, json={'detail': error.model_dump(mode='json')}, headers=headers)


def test_start_failure_decodes_operator_diagnostics_header():
    block = {'error_class': 'InsufficientPrivilege', 'sqlstate': '42501', 'stage': 'session insert',
             'message': 'row-level security for table "[identifier]"', 'role': 'delivery_app',
             'sub_present': False, 'session_id_present': False, 'session_active_for_sub': 'UNKNOWN'}
    client = HttpEngineClient('http://engine', transport=httpx.MockTransport(lambda r: _diag_response(503, block=block)))
    with pytest.raises(EngineClientError) as caught:
        client.start(StartSessionRequest(task_id='t', idea='idea', model_route='m'))
    assert caught.value.operator_diagnostics == block
    assert caught.value.http_status == 503


def test_operator_diagnostics_header_absent_or_damaged_yields_none():
    client = HttpEngineClient('http://engine', transport=httpx.MockTransport(lambda r: _diag_response(503)))
    with pytest.raises(EngineClientError) as caught:
        client.start(StartSessionRequest(task_id='t', idea='idea', model_route='m'))
    assert caught.value.operator_diagnostics is None
    for damaged in ('not-base64!!!', 'e30' + 'x' * 3000,
                    __import__('base64').urlsafe_b64encode(b'[1,2,3]').decode()):
        client = HttpEngineClient('http://engine', transport=httpx.MockTransport(lambda r: _diag_response(503, raw_header=damaged)))
        with pytest.raises(EngineClientError) as caught:
            client.start(StartSessionRequest(task_id='t', idea='idea', model_route='m'))
        assert caught.value.operator_diagnostics is None, damaged[:20]
