"""Restricted PG audit reads, owner-only resolution and private browser journey."""
import json,uuid
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.operator_reads import OperatorReadStore
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.audit_view import list_rows,project
from test_attempt_ledger import Q

@pytest.fixture
def audit_workspace(pg_workspace_factory):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        store=w.store;owner=w.scope.owner_id
        with w.admin() as c:
            c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(owner,'audit-fixture',['logs.read','sessions.read','analytics.read']))
            c.execute('INSERT INTO admission_limits VALUES(%s,%s,1000,300,700,300,700,60,100,3)',(owner,'fixture'))
        reader=OperatorReadStore(w.dsn.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(owner,'audit-fixture',frozenset({'logs.read','sessions.read','analytics.read'})))
        task=store.create_task('Audit task');tid=task['task_id'];store.set_engine_session(tid,str(uuid.uuid4()))
        job=store.enqueue_worker(tid,'phase:needs');store.claim_worker(job)
        aid=uuid.uuid4().hex;store.reserve_provider_attempt(job,aid,Q,'audit-request');store.transition_provider_attempt(aid,'start');store.transition_provider_attempt(aid,'uncertain')
        yield w,reader,tid,job,aid
        reader.db.close()
        with w.admin() as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(owner,))

def test_pages_filter_redaction_uncertain_refused_hide_preserves_evidence(audit_workspace):
    w,reader,tid,job,aid=audit_workspace;s=w.store
    event=list_rows(reader,s,owner=w.scope.owner_id,limit=1,event='provider.attempt.uncertain')['events'][0]
    assert event['attempt_id']==aid and not event['actions']['release']['available']
    original=s.provider_attempt(aid)
    with pytest.raises(ValueError):s.resolve_audit_event(event['id'],'cancel','uncertain')
    assert s.provider_attempt(aid)==original
    result=s.resolve_audit_event(event['id'],'hide','uncertain')
    assert result['resolved'] and s.task(tid) is None and s.provider_attempt(aid)==original
    page=list_rows(reader,s,owner=w.scope.owner_id,limit=1)
    assert page['truncated'] and page['next_before'] and page['events'][0]['event']=='audit.resolution'
    older=list_rows(reader,s,owner=w.scope.owner_id,before=page['next_before'],limit=1)
    assert older['events'][0]['id']<page['events'][0]['id']
    projection=project({'id':1,'ts':0,'actor':'test','action':'provider.attempt.uncertain','detail':json.dumps({'attempt_id':'a','prompt':'private full prompt','cookie':'secret'}),'task_id':tid,'owner_id':'other'},w.scope.owner_id)
    assert not projection['current_owner'] and not any(a['available'] for a in projection['actions'].values())
    assert 'private full prompt' not in str(projection) and 'secret' not in str(projection)

def test_cancel_requires_nonrunning_job_and_preserves_money_history(audit_workspace):
    w,reader,tid,job,old=audit_workspace;s=w.store
    aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,Q,'reserved')
    eid=s.audit(w.scope.owner_id,'provider.attempt.release_failed',tid,{'attempt_id':aid})
    with pytest.raises(ValueError):s.resolve_audit_event(eid,'cancel','reserved')
    with w.admin() as c:c.execute("UPDATE worker_jobs SET state='failed' WHERE job_id=%s",(job,))
    event=[x for x in list_rows(reader,s,owner=w.scope.owner_id)['events'] if x['id']==eid][0]
    assert event['actions']['cancel']['available'],event
    assert s.resolve_audit_event(eid,'cancel','reserved')['resolved']
    assert s.provider_attempt(aid)['state']=='cancelled' and s.provider_attempt(old)['state']=='uncertain'

def test_wrong_owner_cannot_resolve(audit_workspace):
    from delivery_api.tenancy import TenantScope
    w,reader,tid,job,aid=audit_workspace
    eid=w.store.audit_entries(tid)[0]['id']
    # RLS rejects unknown/foreign auth scope, never a cross-user mutation.
    with tenant_context(TenantScope('00000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000098')):
        with pytest.raises(Exception):w.store.resolve_audit_event(eid,'hide')


def test_release_requires_stored_exact_proof_and_never_releases_uncertain(audit_workspace):
    w,reader,tid,job,old=audit_workspace;s=w.store
    aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,Q,'proof');s.transition_provider_attempt(aid,'start')
    proof={'kind':'connect-failure','error_class':'ConnectError','bytes_sent':0,'response_seen':False}
    eid=s.audit(w.scope.owner_id,'provider.attempt.release_failed',tid,{'attempt_id':aid,'evidence':proof})
    with w.admin() as c:c.execute("UPDATE worker_jobs SET state='failed' WHERE job_id=%s",(job,))
    event=[x for x in list_rows(reader,s,owner=w.scope.owner_id)['events'] if x['id']==eid][0]
    assert event['actions']['release']['available']
    assert s.resolve_audit_event(eid,'release','dispatched')['resolved']
    assert s.provider_attempt(aid)['state']=='released' and s.provider_attempt(old)['state']=='uncertain'


def test_proxy_audit_write_is_fixed_confirmed_and_same_origin():
    import httpx
    from fastapi.testclient import TestClient
    from delivery_api.backoffice import create_app
    calls=[]
    def upstream(r):calls.append(r);return httpx.Response(200,json={'resolved':True,'accounting_retained':True})
    client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    url='/api/operations/audit-events/1/resolve';headers={'Authorization':'Bearer fake','Origin':'http://testserver'}
    assert client.post(url,headers={'Authorization':'Bearer fake'},json={'confirm':True,'action':'hide'}).status_code==403
    assert client.post(url,headers=headers,json={'confirm':False,'action':'hide'}).status_code==422
    assert client.post(url,headers=headers,json={'confirm':True,'action':'writeoff'}).status_code==422
    assert client.post(url,headers=headers,json={'confirm':True,'action':'hide','owner_id':'other'}).status_code==422
    assert not calls
    assert client.post(url,headers=headers,json={'confirm':True,'action':'hide'}).status_code==200
    assert calls[0].url.path==url and calls[0].headers['origin']=='http://127.0.0.1:8000'
