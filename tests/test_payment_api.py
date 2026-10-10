from fastapi import FastAPI
from fastapi.testclient import TestClient
from delivery_api.payment_api import create_router


def test_unmounted_or_disabled_routes_are_404_without_auth_processor_or_repo_work():
    def fail(_):raise AssertionError('disabled must not read identity')
    app=FastAPI();app.include_router(create_router(service=object(),owner_resolver=fail))
    client=TestClient(app)
    for method,path in [('POST','/api/payments/purchases'),('POST','/api/payments/webhook'),
        ('POST','/api/payments/purchases/x/checkout'),('POST','/api/payments/purchases/x/capture'),('GET','/api/payments/purchases/x')]:
        assert client.request(method,path,json={'offer_id':'credits-5'}).status_code==404


def test_active_private_factory_keeps_owner_price_and_scale_server_selected():
    from types import SimpleNamespace
    from delivery_api.payment_contract import PurchaseIntent
    from delivery_api.payment_errors import PaymentRejected
    owner='00000000-0000-0000-0000-000000000001'
    class Repo:
        rows={}
        def create_purchase(self,intent):self.rows[intent.purchase_id]={'intent':intent,'state':'awaiting_payment','credit_posted':False}
        def get_purchase(self,id):
            if id not in self.rows:raise PaymentRejected('not found')
            return self.rows[id]
    repo=Repo();service=SimpleNamespace(repo=repo,create=lambda id:{'order_id':'remote','approval_url':'test'},capture=lambda id:{'state':'awaiting_payment','credit_posted':False})
    app=FastAPI();app.include_router(create_router(service=service,owner_resolver=lambda _:{'owner_id':owner,'units_per_credit':1000},mutation_guard=lambda _:None,enabled=True))
    client=TestClient(app)
    assert client.post('/api/payments/purchases',json={'offer_id':'credits-5','owner_id':'other','amount_minor':1}).status_code==422
    response=client.post('/api/payments/purchases',json={'offer_id':'credits-5'})
    assert response.status_code==200 and response.json()['amount_minor']==500
    id=response.json()['purchase_id'];assert repo.rows[id]['intent'].credit_units==5000
    foreign=PurchaseIntent('foreign','00000000-0000-0000-0000-000000000002','v','pack','USD',1,1,1000)
    repo.rows['foreign']={'intent':foreign,'state':'fulfilled','credit_posted':True}
    for method,path in [('GET','/api/payments/purchases/foreign'),('POST','/api/payments/purchases/foreign/capture'),('POST','/api/payments/purchases/foreign/checkout')]:
        assert client.request(method,path).status_code==404


def test_real_application_has_no_mounted_payment_routes():
    from delivery_api.app import app
    assert not any(str(route.path).startswith('/api/payments') for route in app.routes)
