from types import SimpleNamespace
from delivery_api import app as m,cloud_runtime,cloud_execution

def test_operator_sees_scrubbed_actual_pin_reason_and_configured_repair(monkeypatch):
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(m,'cloud_execution_admitted',lambda:False)
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:None)
    monkeypatch.setattr(cloud_execution,'problems',lambda:['money gateway: Choose one reviewed deepseek model in Back office > Model pins; postgresql://owner:secret@db/data'])
    monkeypatch.setenv('REQUI_PIN_STORE','1')
    monkeypatch.setenv('DELIVERY_BACKOFFICE_URL','http://127.0.0.1:8031/#model-pins')
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake')
    monkeypatch.setattr(m.local_route,'local_preview_enabled',lambda:True)
    reply=m.model_routes(SimpleNamespace(admin=True))
    assert all(not r['available'] for r in reply['routes'])
    for r in reply['routes']:
        assert 'Choose one reviewed deepseek' in r['error']
        assert 'secret' not in r['error'] and 'not installed on this server' not in r['error']
        assert r['repair']['url']=='http://127.0.0.1:8031/#model-pins'
    customer=m.model_routes(SimpleNamespace(admin=False))
    assert all('Choose one' not in r['error'] and 'repair' not in r for r in customer['routes'])
