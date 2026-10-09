"""Installed-wheel fake SDK/Stop smoke. No network provider or user state."""
import json,tempfile,pathlib,asyncio
import httpx
from langchain_openai import ChatOpenAI
from spec_generator.provider_transport import AdmittedTransport,AsyncAdmittedTransport
from spec_generator.wire_binding import wire_clients,register_model
from spec_generator.attempt_admission import admission_gateway,checked_model_invoke
from spec_generator.stop_fence import StopFence,StopRequested
from interview_engine.session_runtime import SessionRuntime
from delivery_api.same_host_producer import SameHostProducerWorker
from vmodel_contracts import CONTRACT_VERSION
assert CONTRACT_VERSION.startswith('0.28.')
import os
assert not os.environ.get('PYTHONPATH'), 'source PYTHONPATH must not override built wheels'
for package in (__import__('delivery_api'),__import__('interview_engine'),__import__('spec_generator'),__import__('vmodel_contracts')):
    assert 'site-packages' in package.__file__, f'not an installed wheel: {package.__name__}'
assert not any(os.environ.get(name) for name in ('DEEPSEEK_API_KEY','OPENAI_API_KEY','SUPABASE_SERVICE_ROLE_KEY','TURSO_AUTH_TOKEN')), 'test image must not receive live secrets'
import os
assert not os.environ.get('PYTHONPATH'), 'source PYTHONPATH must not override built wheels'
for package in (__import__('delivery_api'),__import__('interview_engine'),__import__('spec_generator'),__import__('vmodel_contracts')):
    assert 'site-packages' in package.__file__, f'not an installed wheel: {package.__name__}'
assert not any(os.environ.get(name) for name in ('DEEPSEEK_API_KEY','OPENAI_API_KEY','SUPABASE_SERVICE_ROLE_KEY','TURSO_AUTH_TOKEN')), 'test image must not receive live secrets'
# All imports are installed packages. No repo source path is provided.
class Gateway:
    def __init__(self):self.events=[]
    def reserve_serialized(self,**kw):self.events.append('reserve');return kw['attempt_id']
    def start(self,a):self.events.append('start')
    def settle_raw(self,a,u):assert u['prompt_tokens']==2;self.events.append('settle')
    def uncertain(self,a):self.events.append('uncertain')
with tempfile.TemporaryDirectory() as directory:
    fence=StopFence(pathlib.Path(directory)/'stop.json');gateway=Gateway();calls=[];clients=[]
    def fake(request):
        calls.append(request.content);fence.request_stop()
        return httpx.Response(200,json={'id':'fake','object':'chat.completion','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':'late'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}})
    def factory(**kw):
        g=kw.pop('gateway')
        pair=(httpx.Client(transport=AdmittedTransport(httpx.MockTransport(fake),g,**kw)),httpx.AsyncClient(transport=AsyncAdmittedTransport(httpx.MockTransport(fake),g,**kw)))
        clients.extend(pair);return pair
    try:
        with fence.running(),admission_gateway(gateway,required=True),wire_clients(gateway,factory):
            from spec_generator.wire_binding import model_clients
            args=model_clients(stage='needs',provider='deepseek',model='fake')
            model=register_model(ChatOpenAI(model='fake',api_key='fake-not-used',base_url='https://fake.invalid',max_tokens=10,max_retries=0,**args),stage='needs',provider='deepseek',model='fake')
            for _ in range(2):
                try:checked_model_invoke(model,['fake'],stage='needs',provider='deepseek',model='fake',max_output_tokens=10)
                except StopRequested:pass
                else:raise AssertionError('Stopped result escaped')
        assert len(calls)==1 and gateway.events==['reserve','start','settle'] and fence.state=='STOPPED'
    finally:
        for client in clients:
            if isinstance(client,httpx.Client):client.close()
            else:asyncio.run(client.aclose())
# Accepted stream drains to terminal raw usage despite Stop. Both SDK paths are
# exercised without opening a network transport.
from spec_generator.attempt_admission import checked_model_stream
for streaming in (True,):
    with tempfile.TemporaryDirectory() as directory:
        fence=StopFence(pathlib.Path(directory)/'stop.json');gateway=Gateway();calls=[];clients=[]
        class Chunks(httpx.SyncByteStream):
            def __iter__(self):
                fence.request_stop()
                common={'id':'fake','object':'chat.completion.chunk','model':'fake'}
                events=[common|{'choices':[{'index':0,'delta':{'role':'assistant','content':'late'},'finish_reason':None}]},common|{'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}}]
                raw=b''.join(('data: '+json.dumps(e)+'\n\n').encode() for e in events)+b'data: [DONE]\n\n'
                for i in range(0,len(raw),9):yield raw[i:i+9]
        def fake_stream(request):
            calls.append(request.content)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Chunks())
        def factory(**kw):
            g=kw.pop('gateway')
            pair=(httpx.Client(transport=AdmittedTransport(httpx.MockTransport(fake_stream),g,**kw)),httpx.AsyncClient(transport=AsyncAdmittedTransport(httpx.MockTransport(fake_stream),g,**kw)))
            clients.extend(pair);return pair
        try:
            with fence.running(),admission_gateway(gateway,required=True),wire_clients(gateway,factory):
                args=model_clients(stage='srs',provider='deepseek',model='fake')
                model=register_model(ChatOpenAI(model='fake',api_key='fake-not-used',base_url='https://fake.invalid',max_tokens=10,max_retries=0,**args),stage='srs',provider='deepseek',model='fake')
                received=[]
                try:
                    for chunk in checked_model_stream(model,['fake'],stage='srs',provider='deepseek',model='fake',max_output_tokens=10):received.append(chunk.content)
                except StopRequested:pass
                else:raise AssertionError('Stopped stream escaped')
            assert not received and len(calls)==1 and gateway.events==['reserve','start','settle'] and fence.state=='STOPPED'
        finally:
            for client in clients:
                if isinstance(client,httpx.Client):client.close()
                else:asyncio.run(client.aclose())
print(json.dumps({'result':'passed','built_wheel_imports':True,'contract':CONTRACT_VERSION,'fake_sdk_calls':2,'reservations':2,'settlements':2,'invoke_and_stream_stop_checked':True,'stopped_result_discarded':True,'live_provider_calls':0,'database_migrations_applied':False,'production_gate_enabled':False}))
