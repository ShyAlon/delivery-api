from types import SimpleNamespace
from delivery_api import app as m,cloud_runtime,cloud_execution

def test_customer_route_api_never_exposes_operator_evidence_even_to_admin(monkeypatch):
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(m,'cloud_execution_admitted',lambda:False)
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:None)
    monkeypatch.setattr(cloud_execution,'problems',lambda:['money gateway: Choose one reviewed deepseek model in Back office > Model pins; postgresql://owner:secret@db/data'])
    monkeypatch.setenv('REQUI_PIN_STORE','1')
    monkeypatch.setenv('DELIVERY_BACKOFFICE_URL','http://127.0.0.1:8031/#model-pins')
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake')
    reply=m.model_routes(SimpleNamespace(admin=True))
    assert not reply['routes'][0]['available']
    assert not reply['routes'][1]['available']
    assert reply['default_route'] is None
    for actor in (SimpleNamespace(admin=True),SimpleNamespace(admin=False)):
        customer=m.model_routes(actor)
        for r in customer['routes']:
            assert r['reason']=='runtime_unavailable'
            assert 'runtime is not ready' in r['error']
            assert 'repair' not in r
            assert not any(term in str(r).lower() for term in ('back office','model pins','money gateway','secret','postgresql:','deepseek model'))
