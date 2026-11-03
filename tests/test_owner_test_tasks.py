"""Owner-only immutable marker and guarded content purge on isolated PostgreSQL."""
import uuid
import psycopg
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B,DSN


@pytest.fixture
def operator():
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'test-marker',ARRAY['sessions.operate'],true)",(A.owner_id,))
    yield
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals')


def test_marker_is_created_once_owner_only_and_hidden_from_normal_lists(store,operator):
    with tenant_context(A):
        t=store.create_task('test-'+uuid.uuid4().hex,is_test=True)
        assert t['is_test'] is True
        assert t['task_id'] not in {r['task_id'] for r in store.list_tasks()}
        assert t['task_id'] in {r['task_id'] for r in store.list_test_tasks()}
        with pytest.raises(Exception):
            with store._postgres.transaction() as c:c.execute('UPDATE tasks SET is_test=false WHERE task_id=%s',(t['task_id'],))
    with tenant_context(B):
        with pytest.raises(Exception):store.create_task('forbidden-'+uuid.uuid4().hex,is_test=True)
        assert store.task(t['task_id']) is None
        with pytest.raises(Exception):store.list_test_tasks()


def test_normal_task_cannot_be_purged_and_failure_keeps_test_content(store,operator):
    with tenant_context(A):
        normal=store.create_task('real-'+uuid.uuid4().hex)
        t=store.create_task('test-'+uuid.uuid4().hex,'private brief',is_test=True)
        with pytest.raises(Exception):store.purge_test_task(normal['task_id'],confirm=True)
        with pytest.raises(Exception):store.purge_test_task(t['task_id'],confirm=False)
        with pytest.raises(Exception):store.purge_test_task(t['task_id'],confirm=True)
        assert store.task(t['task_id'])['brief']=='private brief'


def completed_test(store):
    with tenant_context(A):
        t=store.create_task('test-'+uuid.uuid4().hex,'private brief',is_test=True)
        sid=str(uuid.uuid4());store.set_engine_session(t['task_id'],sid)
        for n in range(2):
            job='purge-'+uuid.uuid4().hex
            with psycopg.connect(DSN) as c:
                c.execute("INSERT INTO worker_jobs(job_id,owner_id,auth_session_id,task_id,session_id,generation,kind,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,1,%s,'done',1,2)",(job,A.owner_id,A.session_id,t['task_id'],sid,'interview.start' if n==0 else 'interview.answer'))
            store.retain_decision_review(t['task_id'],'child-job:'+job,'child-completion-v1',{'reaped':True})
            store.retain_decision_review(t['task_id'],'child-job:'+job,'child-operation-v1',{'operation':{'kind':'interview.start' if n==0 else 'interview.answer'}})
        store.add_event(t['task_id'],'private','private prompt')
        return t,sid


def test_successful_purge_is_scoped_idempotent_and_keeps_identity_and_audit(store,operator):
    t,sid=completed_test(store)
    with tenant_context(B):other=store.create_task('other-'+uuid.uuid4().hex,'untouched')
    with tenant_context(A):
        out=store.purge_test_task(t['task_id'],confirm=True)
        assert out['purged'] and out['accounting_retained'] and not out['already_purged']
        assert store.purge_test_task(t['task_id'],confirm=True)['already_purged']
        assert store.task(t['task_id']) is None
    with psycopg.connect(DSN) as c:
        row=c.execute('SELECT brief,is_test,test_purged_at FROM tasks WHERE task_id=%s',(t['task_id'],)).fetchone()
        assert row[0:2]==('',True) and row[2]>0
        assert c.execute('SELECT count(*) FROM events WHERE task_id=%s',(t['task_id'],)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM worker_jobs WHERE task_id=%s',(t['task_id'],)).fetchone()[0]==2
        assert c.execute("SELECT count(*) FROM audit_log WHERE task_id=%s AND action='test.content_purged'",(t['task_id'],)).fetchone()[0]==1
    with tenant_context(B):assert store.task(other['task_id'])['brief']=='untouched'


def test_live_or_unsettled_or_failed_task_keeps_all_content(store,operator):
    for kind in ('running','failed','lease','unsettled'):
        t,sid=completed_test(store)
        with psycopg.connect(DSN) as c:
            if kind in ('running','failed'):
                c.execute('UPDATE worker_jobs SET state=%s WHERE task_id=%s',(kind,t['task_id']))
            elif kind=='lease':c.execute("UPDATE engine_sessions SET placement_instance='active',lease_expires_at=9999999999 WHERE session_id=%s",(sid,))
            else:
                job=c.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s LIMIT 1',(t['task_id'],)).fetchone()[0]
                c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'test','{}','test','uncertain',1,1,1,1)",('a-'+uuid.uuid4().hex,A.owner_id,job))
        with tenant_context(A):
            with pytest.raises(Exception):store.purge_test_task(t['task_id'],confirm=True)
            assert store.task(t['task_id'])['brief']=='private brief'
            assert store.events(t['task_id'])


def test_signed_in_api_marks_at_creation_and_rejects_customer_marker(monkeypatch,pg_workspace_factory):
    from fastapi.testclient import TestClient
    import delivery_api.app as module
    with pg_workspace_factory() as w:
        with w.admin() as c:
            c.execute('DELETE FROM operator_principals')
            c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'test-api',ARRAY['sessions.operate'],true)",(w.scope.owner_id,))
        monkeypatch.setattr(module,'store',w.store)
        try:
            with TestClient(module.app) as client:
                normal=client.post('/api/tasks',headers=w.headers,json={'title':'normal-'+uuid.uuid4().hex,'is_test':True})
                assert normal.status_code==422
                made=client.post('/api/operations/test-tasks',headers=w.headers,json={'title':'owner-test-'+uuid.uuid4().hex,'brief':'fixture'})
                assert made.status_code==201,made.text
                tid=made.json()['task_id'];assert made.json()['is_test'] is True
                assert tid not in {t['task_id'] for t in client.get('/api/tasks',headers=w.headers).json()['tasks']}
                assert tid in {t['task_id'] for t in client.get('/api/operations/test-tasks',headers=w.headers).json()['tasks']}
                assert client.get('/api/tasks/'+tid+'/artifacts.zip',headers=w.headers).status_code==409
                assert client.post('/api/operations/test-tasks/'+tid+'/purge',headers=w.headers,json={'confirm':True}).status_code==409
        finally:
            with w.admin() as c:c.execute('DELETE FROM operator_principals')


def test_settled_money_and_audit_survive_purge_unchanged(store,operator):
    t,sid=completed_test(store);aid='settled-'+uuid.uuid4().hex
    with psycopg.connect(DSN) as c:
        job=c.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s LIMIT 1',(t['task_id'],)).fetchone()[0]
        c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,created_at,updated_at) VALUES(%s,%s,%s,'test','{}','test','settled',1,2,1,2,1,2)",(aid,A.owner_id,job))
        c.execute("INSERT INTO credit_charges(attempt_id,owner_id,state,charged_units,created_at,settled_at) VALUES(%s,%s,'settled',2,1,2)",(aid,A.owner_id))
        before=c.execute('SELECT row_to_json(p) FROM provider_attempts p WHERE attempt_id=%s',(aid,)).fetchone()[0]
    with tenant_context(A):assert store.purge_test_task(t['task_id'],confirm=True)['accounting_retained']
    with psycopg.connect(DSN) as c:
        assert c.execute('SELECT row_to_json(p) FROM provider_attempts p WHERE attempt_id=%s',(aid,)).fetchone()[0]==before
        assert c.execute('SELECT charged_units FROM credit_charges WHERE attempt_id=%s',(aid,)).fetchone()[0]==2
        assert c.execute('SELECT is_test FROM tasks WHERE task_id=%s',(t['task_id'],)).fetchone()[0] is True


def test_purge_has_no_broad_delete_grants_and_refuses_shared_threads(store,operator):
    t,sid=completed_test(store)
    with tenant_context(A):
        with store._postgres.transaction() as c:
            for table in ('checkpoints','checkpoint_blobs','generator_snapshots','staged_provider_responses','artifact_bytes'):
                assert c.execute('SELECT has_table_privilege(current_user,%s,\'DELETE\') AS allowed',('public.'+table,)).fetchone()['allowed'] is False
        other=store.create_task('shared-'+uuid.uuid4().hex)
        other_sid=str(uuid.uuid4());store.set_engine_session(other['task_id'],other_sid)
    with psycopg.connect(DSN) as c:
        c.execute('UPDATE engine_sessions SET published_thread_id=%s WHERE session_id=%s',(sid,other_sid))
    with tenant_context(A):
        with pytest.raises(Exception):store.purge_test_task(t['task_id'],confirm=True)
        assert store.task(t['task_id'])['brief']=='private brief'


def test_success_purge_deletes_checkpoint_payload(store,operator):
    t,sid=completed_test(store);blob=b'private checkpoint'
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO checkpoint_blobs(thread_id,checkpoint_ns,channel,version,type,blob) VALUES(%s,'','private','1','bytes',%s)",(sid,blob))
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE engine_sessions SET placement_instance='released-instance',lease_expires_at=1 WHERE session_id=%s",(sid,))
    with tenant_context(A):store.purge_test_task(t['task_id'],confirm=True)
    with psycopg.connect(DSN) as c:
        assert c.execute('SELECT count(*) FROM checkpoint_blobs WHERE thread_id=%s',(sid,)).fetchone()[0]==0


def test_compact_breakdown_is_numeric_owned_and_pre_purge(store,operator):
    t,sid=completed_test(store)
    with tenant_context(A):
        value=store.test_task_breakdown(t['task_id'])
        assert value['total_posted_units']=='0'
        assert value['total_exact_credits']=='0'
        assert len(value['children'])==2 and all(x['reaped'] for x in value['children'])
        assert 'private prompt' not in str(value) and 'private brief' not in str(value)
    with tenant_context(B),pytest.raises(Exception):store.test_task_breakdown(t['task_id'])
    with tenant_context(A):
        store.purge_test_task(t['task_id'],confirm=True)
        with pytest.raises(KeyError):store.test_task_breakdown(t['task_id'])


def test_breakdown_reconciles_tokens_compute_and_actual_units(store,operator):
    from delivery_api.retail_pricing import RetailRates
    t,sid=completed_test(store)
    try:
        with tenant_context(A):
            with store._postgres.transaction() as c:
                job=c.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s LIMIT 1',(t['task_id'],)).fetchone()['job_id']
            with psycopg.connect(DSN) as c:c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1000000000,1000000000,0) ON CONFLICT(owner_id) DO NOTHING',(A.owner_id,))
            rates=RetailRates.from_environment({}).snapshot()
            store.post_retail_contribution(key='token:'+job,job_id=job,phase='intent',kind='tokens',input_tokens=3207,output_tokens=315,rates=rates)
            store.post_retail_contribution(key='compute:'+job,job_id=job,phase='intent',kind='compute',deterministic_ms=52440,rates=rates)
            out=store.test_task_breakdown(t['task_id'])
            assert out['parts']['tokens']['posted_units']=='44025000'
            assert out['parts']['compute']['posted_units']=='2622000'
            assert out['parts']['compute']['compute_ms']=='52440'
            assert out['total_posted_units']=='46647000'
            from decimal import Decimal
            assert Decimal(out['total_exact_credits'])==Decimal('0.046647')
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))
