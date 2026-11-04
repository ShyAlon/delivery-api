"""Offline customer page, exact reviewed catalog, production payment fence intact."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from delivery_api.app import app,cloud_execution_admitted
from delivery_api.payment_offers import OFFERS,OFFERS_VERSION
ROOT=Path(__file__).parents[1]

def test_catalog_tracks_reviewed_server_offers_without_operator_data():
 body=json.loads((ROOT/'src/delivery_api/web/credit-offers.json').read_text())
 assert body['version']==OFFERS_VERSION and body['currency']=='USD'
 assert {x['offer_id']:(x['credits'],x['amount_minor']) for x in body['offers']}==OFFERS
 assert [x['discount'] for x in body['offers']]==[0,4,8,10]
 assert set(body)=={'version','currency','offers'}
 for row in body['offers']:assert set(row)=={'offer_id','credits','amount_minor','discount'}

def _case_customer_shell_and_payment_mutations_remain_unmounted():
 with TestClient(app) as c:
  r=c.get('/credits?view=review&offer=credits-50');assert r.status_code==200
  assert '/assets/purchase.css' in r.text and 'no-store' in r.headers['cache-control']
  for path in ('/api/payments/purchases','/api/payments/webhook'):
   assert c.post(path,json={'offer_id':'credits-5'}).status_code==404
  assert c.get('/api/payments/purchases/arbitrary').status_code==404
  assert cloud_execution_admitted() is False


def test_customer_shell_and_payment_mutations_remain_unmounted(pg_scoped_workspace,pg_task_app,monkeypatch):
 c=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers)
 monkeypatch.setitem(globals(),'TestClient',lambda *a,**kw:c)
 _case_customer_shell_and_payment_mutations_remain_unmounted()
