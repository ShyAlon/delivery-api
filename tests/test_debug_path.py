"""The browser can show where time goes and which server record belongs to an error; cloud screens do not call model settings."""
import re
import pytest
from fastapi.testclient import TestClient
import delivery_api.app as module
from delivery_api import config


@pytest.fixture
def client():
    return TestClient(module.app)


def test_client_log_keys_cannot_collide_with_server_fields(client):
    reply=client.post('/api/client-log',json={'kind':'api.error','message':'m','path':'/x','detail':{'actor':'forged','path':'/forged','client':'1.2.3.4','endpoint':'/e'}})
    assert reply.status_code==204


def test_every_response_carries_a_reference_and_server_time(client):
    one,two=client.get('/health'),client.get('/health')
    for reply in (one,two):
        assert re.fullmatch(r'[0-9a-f]{8}',reply.headers['x-request-id'])
        assert re.fullmatch(r'app;dur=\d+',reply.headers['server-timing'])
    assert one.headers['x-request-id']!=two.headers['x-request-id']


def test_assets_are_revalidated_not_refetched_and_pages_stay_uncached(client):
    first=client.get('/assets/app.js')
    assert first.status_code==200 and first.headers['cache-control']=='no-cache' and first.headers['etag']
    again=client.get('/assets/app.js',headers={'If-None-Match':first.headers['etag']})
    assert again.status_code==304 and again.content==b''
    assert client.get('/').headers['cache-control']=='no-store, max-age=0'


@pytest.mark.parametrize('open_now',[False,True])
def test_cloud_model_routes_are_managed_and_start_only_when_execution_is_open(monkeypatch,open_now):
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:open_now)
    reply=module.model_routes(None)
    assert reply['managed'] is True and reply['default_route']=={'route':'deepseek'}
    (route,)=reply['routes']
    assert route['id']=='deepseek' and route['managed'] is True and route['available'] is open_now
    assert ('error' in route)==(not open_now)


def _web(name):return (module.WEB_DIR/name).read_text()


def test_stylesheet_does_not_block_on_a_third_party_import_and_modules_load_in_parallel():
    assert '@import' not in _web('style.css')
    html=_web('index.html')
    assert 'fonts.googleapis.com' not in html and 'fonts.gstatic.com' not in html   # fonts are self-hosted: no third-party stylesheet request
    fonts=_web('fonts.css')
    assert 'display=swap' in fonts.replace(': swap',':swap') or 'font-display:swap' in fonts.replace(' ','')
    seen,todo=set(),['app.js']
    while todo:
        name=todo.pop()
        if name in seen:continue
        seen.add(name);todo+=re.findall(r"from '\./([a-z0-9-]+\.js)'",_web(name))
    preloaded=set(re.findall(r'<link rel="modulepreload" href="/assets/([a-z0-9-]+\.js)">',html))
    assert seen-{'app.js'}==preloaded       # every module the app imports is requested at once, and nothing stale is listed
