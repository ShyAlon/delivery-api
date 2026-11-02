"""Real parent socket expiry; no paid service or child timeout authority."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
import pytest
from delivery_api.provider_http import ParentProviderHTTP


def test_parent_deadline_replaces_client_hour_default_and_keeps_bytes(monkeypatch):
    monkeypatch.setenv('VMODEL_LLM_CALL_TIMEOUT_S','23')
    seen=[]
    def send(request):
        seen.append(request)
        assert request.extensions['timeout']=={'connect':23.,'read':23.,'write':23.,'pool':23.}
        assert request.content==b'{"model":"fixture"}'
        return httpx.Response(200,headers={'content-type':'application/json'},content=b'{}')
    with httpx.Client(transport=httpx.MockTransport(send),timeout=3600) as client:
        ParentProviderHTTP(client,'https://fixture.invalid',{}).send(b'{"model":"fixture"}')
    assert len(seen)==1


def test_stream_parent_uses_stall_guard_not_hour(monkeypatch):
    monkeypatch.setenv('VMODEL_LLM_CALL_TIMEOUT_S','600')
    monkeypatch.setenv('VMODEL_LLM_STALL_TIMEOUT_S','180')
    def send(request):
        assert request.extensions['timeout']['read']==180.
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(send),timeout=3600) as client:
        ParentProviderHTTP(client,'https://fixture.invalid',{}).send(b'{"stream":true}')


@pytest.mark.parametrize('value',['0','-1','nan','inf','garbage'])
def test_invalid_parent_bound_refuses_before_http(monkeypatch,value):
    monkeypatch.setenv('VMODEL_LLM_CALL_TIMEOUT_S',value)
    with httpx.Client() as client,pytest.raises(ValueError,match='VMODEL_LLM_CALL_TIMEOUT_S'):
        ParentProviderHTTP(client,'https://fixture.invalid',{}).send(b'{}')


def test_real_held_http_times_out_and_attempt_stays_uncertain_without_retry(monkeypatch):
    from test_child_provider_broker import setup,request
    monkeypatch.setenv('VMODEL_LLM_CALL_TIMEOUT_S','0.08')
    entered=threading.Event();release=threading.Event();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers['Content-Length'])))
            entered.set();release.wait(2)
            try:
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        broker,g,j,_=setup()
        with httpx.Client(trust_env=False,timeout=3600) as client:
            broker.http=ParentProviderHTTP(client,f'http://127.0.0.1:{server.server_port}',{})
            from delivery_api.child_provider_broker import BrokerPaused
            with pytest.raises(BrokerPaused):broker.dispatch(request())
            assert entered.is_set() and g.calls==['reserve','start','uncertain']
            with pytest.raises(BrokerPaused):broker.dispatch(request())
            assert len(calls)==1
    finally:
        release.set();server.shutdown();server.server_close();thread.join(2)
