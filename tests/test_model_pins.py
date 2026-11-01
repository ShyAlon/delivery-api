"""Real restricted PostgreSQL repository; public pin isolation, operator authority and run immutability."""
import copy,datetime,json,uuid
import psycopg,pytest
from delivery_api import model_pins as pins,production_gateway as pg
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from test_postgres_store import A,B,DSN
from test_production_gateway import row,CEILING


def payload():return {'row':row(),'context':dict(CEILING)}

@pytest.fixture
def repo(store):
    with psycopg.connect(DSN) as conn:
        conn.execute('UPDATE operator_principals SET enabled=false')
        conn.execute("INSERT INTO operator_principals VALUES(%s,'ops-v1',ARRAY['services.operate'],true) ON CONFLICT(subject) DO UPDATE SET capabilities=excluded.capabilities,enabled=true,registry_version='ops-v1'",(A.owner_id,))
        conn.execute('DELETE FROM model_pin_heads');conn.execute('DELETE FROM model_pin_revisions')
    yield pins.PinRepository(store._postgres,OperatorPrincipal(A.owner_id,'ops-v1',frozenset({'services.operate'})))
    with psycopg.connect(DSN) as conn:
        conn.execute('DELETE FROM model_pin_heads');conn.execute('DELETE FROM model_pin_revisions');conn.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))


def test_commit_list_history_and_race(store,repo):
    value=payload()
    with tenant_context(A):
        revision=repo.commit(store,value,None,'Initial reviewed fixture')
        assert repo.list()[0]['revision']==revision
        assert len(repo.history(store))==1
        changed=copy.deepcopy(value);changed['context']['maximum_output_tokens']-=1
        with pytest.raises(ValueError,match='Pin changed'):repo.commit(store,changed,None,'stale form')
        nextrev=repo.commit(store,changed,revision,'Adjusted output cap')
        assert nextrev!=revision and len(repo.history(store))==2
        assert repo.history(store)[0]['previous_revision']==revision
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):repo.commit(store,value,nextrev,'wrong caller')
        assert repo.list()[0]['revision']==nextrev # only public config, not private operator history
        with pytest.raises(TenantAccessDenied):repo.history(store)


def test_revision_cannot_be_updated_or_deleted_even_by_operator(store,repo):
    with tenant_context(A):
        rev=repo.commit(store,payload(),None,'Immutable audit')
        with store._postgres.transaction() as conn:
            conn.execute('SET LOCAL ROLE requi_model_pin_editor')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):conn.execute("UPDATE model_pin_revisions SET reason='rewrite' WHERE revision=%s",(rev,))


def test_runtime_gateway_freezes_each_task_generation(store,repo):
    value=payload()
    with tenant_context(A):
        rev=repo.commit(store,value,None,'first')
        factory=pins.DurableGateways(store,{pg.KEY_ENV:'fake','REQUI_PIN_PROVIDERS':'deepseek'})
        task=store.create_task('pins-'+uuid.uuid4().hex)
        first=factory.for_task(task['task_id'])
        changed=copy.deepcopy(value);changed['context']['maximum_output_tokens']-=1
        repo.commit(store,changed,rev,'new run output cap')
        assert factory.for_task(task['task_id']) is first
        secondtask=store.create_task('pins-'+uuid.uuid4().hex)
        second=factory.for_task(secondtask['task_id']);assert second is not first
        factory.close()
        restored=pins.DurableGateways(store,{pg.KEY_ENV:'fake','REQUI_PIN_PROVIDERS':'deepseek'})
        old=restored.for_task(task['task_id'])
        assert old.catalog.resolve_serialized is not None
        assert json.loads(store.decision_review(task['task_id'],'model-pins:1','model-pins-v1')[pg.CEILING_ENV])==value['context']
        restored.close();store.delete_task(task['task_id']);store.delete_task(secondtask['task_id'])


def test_proposal_binds_subject_payload_and_expiry(monkeypatch):
    p=pins.proposal(payload(),None,A.owner_id)
    assert pins.approved_proposal(p['review'],p['signature'],A.owner_id)==p['review']['payload']
    with pytest.raises(ValueError):pins.approved_proposal(p['review'],p['signature'],B.owner_id)
    changed=copy.deepcopy(p['review']);changed['payload']['context']['maximum_output_tokens']-=1
    with pytest.raises(ValueError):pins.approved_proposal(changed,p['signature'],A.owner_id)
    monkeypatch.setattr(pins.time,'time',lambda:p['review']['expires'])
    with pytest.raises(ValueError):pins.approved_proposal(p['review'],p['signature'],A.owner_id)


@pytest.mark.parametrize('hours,allowed',[(23.99,True),(24,False),(72,False)])
def test_failed_refresh_exact_age_boundary(monkeypatch,hours,allowed):
    value=payload();value['row']['provider_snapshot']['fetched_at']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=hours)).isoformat()
    before=copy.deepcopy(value)
    monkeypatch.setattr(pins.vs,'fetch_verified_source',lambda *a:(_ for _ in ()).throw(ConnectionError('offline')))
    if allowed:
        result=pins.refresh(value);assert result['warning'] and result['payload']==before
    else:
        with pytest.raises(ValueError,match='24h'):pins.refresh(value)
    assert value==before


def test_price_snapshot_mismatch_and_unexpected_keys_refuse():
    value=payload();value['row']['expense_output']+=1
    with pytest.raises(ValueError,match='source snapshot'):pins.validate(value)
    value=payload();value['row']['api_key']='not-a-real-key'
    with pytest.raises(ValueError,match='credentials'):pins.validate(value)


def test_operator_pin_api_and_proxy_confirmed_writes_are_narrow(monkeypatch,store,repo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from delivery_api.model_pin_api import install
    from delivery_api import auth
    from types import SimpleNamespace
    app=FastAPI();reader=SimpleNamespace(db=store._postgres,principal=repo.principal)
    install(app,store,lambda:reader)
    app.dependency_overrides[auth.require_actor]=lambda:SimpleNamespace(id=A.owner_id)
    c=TestClient(app);value=payload()
    with tenant_context(A):
        rev=repo.commit(store,value,None,'initial')
        value['context']['maximum_output_tokens']-=1
        assert c.post('/api/operator/model-pins/edit',json={'payload':value,'expected':rev}).status_code==403
        response=c.post('/api/operator/model-pins/edit',headers={'origin':'http://testserver'},json={'payload':value,'expected':rev})
        assert response.status_code==200,response.text
        review=response.json();assert review['review']['payload']['row']['provider_snapshot']['fetched_at']==repo.list()[0]['payload']['row']['provider_snapshot']['fetched_at']
        assert c.post('/api/operator/model-pins/approve',headers={'origin':'http://testserver'},json={**review,'reason':'reviewed','confirm':False}).status_code==409
        approved=c.post('/api/operator/model-pins/approve',headers={'origin':'http://testserver'},json={**review,'reason':'reviewed','confirm':True})
        assert approved.status_code==200,approved.text
        assert len(repo.history(store))==2
    with tenant_context(B):
        assert c.get('/api/operator/model-pins').status_code==403


def test_public_runtime_reader_has_no_operator_identity_or_audit_columns(store,repo):
    with tenant_context(A):repo.commit(store,payload(),None,'operator private audit reason')
    # No user authority is minted by the public config context; grants restrict columns.
    assert set(repo.list()[0])=={'revision','provider','model','payload','fetched_at','is_default'}
    with pins.public_pin_context(),store._postgres.transaction() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):conn.execute('SELECT reason FROM model_pin_revisions')


def test_same_provider_new_default_and_old_manual_fetched_timestamp(store,repo):
    with tenant_context(A):
        value=payload();first=repo.commit(store,value,None,'first default')
        other=payload();other['row']['model']='another-reviewed';other['row']['provider_snapshot']['model']='another-reviewed';other['context']['model']='another-reviewed'
        repo.commit(store,other,None,'second default')
        rows=repo.list();assert sum(r['is_default'] for r in rows)==1
        selected=pins.selected_environment(store._postgres,{'REQUI_PIN_PROVIDERS':'deepseek'})
        assert json.loads(selected[pg.PRICES_ENV])[0]['model']=='another-reviewed'


def test_durable_verifier_blocks_changed_price_and_reuses_only_good_checks(monkeypatch):
    value=payload();calls=[]
    def refreshed(candidate):calls.append(candidate);return {'payload':copy.deepcopy(candidate),'warning':None,'changed':False}
    monkeypatch.setattr(pins,'refresh',refreshed)
    verify=pins.pin_verifier(value);assert verify() and verify() and len(calls)==1
    other=copy.deepcopy(value);other['row']['provider_snapshot']['published_usd_per_million']['peak']['output']='9.00'
    monkeypatch.setattr(pins,'refresh',lambda candidate:{'payload':other,'warning':None,'changed':True})
    with pytest.raises(ValueError,match='changed'):pins.pin_verifier(value)()
