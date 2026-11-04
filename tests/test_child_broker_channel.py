"""Private broker channel with real sockets, fake parent HTTP only."""
import asyncio
import json
import socket
import threading
import httpx
import pytest
from requi_child_contract.broker import BrokerClient,BrokerUnavailable,BrokerTransport,AsyncBrokerTransport,clients
from delivery_api.child_broker_channel import provider_broker_channel
from test_child_provider_broker import setup,BODY


def test_sdk_bytes_cross_private_channel_but_headers_and_url_do_not():
    broker,g,j,calls=setup()
    with provider_broker_channel(broker) as channel:
        client=BrokerClient(channel.fileno(),next_call_id=lambda stage:'stable-call-1')
        sync,async_client=clients(client,stage='intent',provider='deepseek',model='deepseek-flash')
        response=sync.post('https://ignored.invalid/v1/chat/completions',headers={'Authorization':'not-forwarded'},json={'model':'deepseek-flash','max_tokens':10})
        assert response.content==BODY
        assert len(calls)==1 and calls[0].headers['authorization']=='Bearer parent-only'
        sync.close();asyncio.run(async_client.aclose());client.close()


def test_channel_loss_permanently_poisons_client_no_retry():
    parent,child=socket.socketpair();count=[]
    def serve():
        count.append(parent.recv(65536));parent.close()
    thread=threading.Thread(target=serve);thread.start()
    client=BrokerClient(child.fileno(),next_call_id=lambda stage:'call-1');child.close()
    with pytest.raises(BrokerUnavailable):client.request(stage='intent',provider='deepseek',model='m',raw=b'{}')
    with pytest.raises(BrokerUnavailable,match='already lost'):client.request(stage='intent',provider='deepseek',model='m',raw=b'{}')
    client.close();thread.join(2);assert len(count)==1


def test_engine_and_generator_explicit_hooks_bind_without_local_transport():
    from interview_engine import wire_binding as engine
    from spec_generator import wire_binding as generator
    broker,g,j,calls=setup()
    with provider_broker_channel(broker) as channel:
        client=BrokerClient(channel.fileno(),next_call_id=lambda stage:'stable-call-1')
        for module in (engine,generator):
            with module.broker_clients(client):
                pair=module.model_clients(stage='intent',provider='deepseek',model='deepseek-flash')
                assert isinstance(pair['http_client']._transport,BrokerTransport)
                assert isinstance(pair['http_async_client']._transport,AsyncBrokerTransport)
                obj=object();module.register_model(obj,stage='intent',provider='deepseek',model='deepseek-flash')
                assert module.wire_model(obj,stage='intent',provider='deepseek',model='deepseek-flash')
                with pytest.raises(module.AdmissionUnavailable):module.wire_model(obj,stage='other',provider='deepseek',model='deepseek-flash')
                pair['http_client'].close();asyncio.run(pair['http_async_client'].aclose())
            assert module.model_clients(stage='intent',provider='deepseek',model='deepseek-flash') is None
        client.close()


def test_async_broker_request_uses_same_parent_boundary():
    broker,g,j,calls=setup()
    with provider_broker_channel(broker) as channel:
        client=BrokerClient(channel.fileno(),next_call_id=lambda stage:'async-call-1')
        async def go():
            async with httpx.AsyncClient(transport=AsyncBrokerTransport(client,stage='intent',provider='deepseek',model='deepseek-flash')) as http:
                reply=await http.post('https://ignored.invalid/chat/completions',json={'model':'deepseek-flash','max_tokens':10})
                assert reply.content==BODY
        asyncio.run(go());client.close()
    assert len(calls)==1


def test_real_child_provider_and_compute_boundary_without_child_credentials():
    import os,subprocess,sys
    from pathlib import Path
    from types import SimpleNamespace
    from delivery_api.child_compute_channel import ChildComputeAccounting,child_compute_channel
    from test_child_compute_channel import Store
    from test_child_provider_broker import Journal
    broker,g,j,calls=setup();store=Store()
    accounting=ChildComputeAccounting(store,broker.operation,rates=SimpleNamespace(snapshot=lambda:{}),phase='intent',journal=Journal())
    with provider_broker_channel(broker) as provider_fd,child_compute_channel(accounting) as compute_fd:
        result=subprocess.run([sys.executable,str(Path(__file__).parent/'fixtures/child_money_fixture.py'),str(provider_fd.fileno()),str(compute_fd.fileno())],
            pass_fds=(provider_fd.fileno(),compute_fd.fileno()),env={'PATH':os.defpath,'LANG':'C.UTF-8'},timeout=5,capture_output=True)
        assert result.returncode==0,result.stderr.decode()
    assert len(calls)==1 and accounting.settled
    assert [e['kind'] for e in accounting.transcript]==['start','wait.begin','wait.end','finish']
    assert accounting.events.milliseconds()>=0


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}\n',b'{"a":NaN}\n',b'{}',b'x'*4097+b'\n'])
def test_shared_channel_rejects_bad_frame_before_protocol_handler(raw):
    from delivery_api.child_channel import json_channel
    seen=[]
    with json_channel(lambda value:seen.append(value) or b'ok',limit=4096,name='fixture',quiescence_error=RuntimeError('not reaped')) as fd:
        fd.settimeout(2);fd.sendall(raw);fd.shutdown(socket.SHUT_WR)
        try:assert fd.recv(20)==b''
        except ConnectionResetError:pass
    assert seen==[]


def test_parent_transport_cause_is_safe_and_visible_when_child_only_gets_eof(capsys):
    def fail(request):
        try:raise __import__('ssl').SSLCertVerificationError('private certificate/host')
        except Exception as error:raise httpx.ConnectError('private provider url/token') from error
    broker,g,j,calls=setup(fail)
    with provider_broker_channel(broker) as channel:
        client=BrokerClient(channel.fileno(),next_call_id=lambda stage:'stable-call-1')
        with pytest.raises(BrokerUnavailable):client.request(stage='intent',provider='deepseek',model='deepseek-flash',raw=b'{"model":"deepseek-flash","max_tokens":10}')
        client.close()
    text=capsys.readouterr().err
    assert 'ConnectError' in text and 'SSLCertVerificationError' in text
    assert 'private' not in text and len(calls)==1
