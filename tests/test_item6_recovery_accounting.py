import json,sys,time,uuid
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from test_retail_ledger import account
from test_attempt_ledger import ledger
from test_postgres_store import DSN,A,B,store
from test_item6_recovery_plan import fixture
from recovery_plan import accounting_preview
from recovery_accounting import reconcile,waive_unknown_compute
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
pytestmark=pytest.mark.skipif(not DSN,reason='isolated migrated PostgreSQL required')

@pytest.fixture
def failed(account):
    import psycopg
    s,tid,job=account;a,j,c=fixture();aid=uuid.uuid4().hex;a.update(attempt_id=aid,job_id=job,owner_id=A.owner_id)
    with tenant_context(A):
        s.begin_retail_compute(job+':compute',job,'intent',a['quote']['retail_formula'])
        s.finish_retail_compute(job+':compute',None)
        s.finish_worker(job,failed=True)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at,provider_started_at,raw_usage,raw_usage_received_at) VALUES(%s,%s,%s,'fixture',%s::jsonb,'fixture','uncertain',150000000,87500000,%s,%s,%s,%s::jsonb,%s)",(aid,A.owner_id,job,json.dumps(a['quote']),a['provider_started_at'],time.time(),a['provider_started_at'],json.dumps(a['raw_usage']),a['raw_usage_received_at']))
        db.execute("INSERT INTO credit_charges(attempt_id,owner_id,state,created_at,units_per_credit) VALUES(%s,%s,'pending',%s,1000000000)",(aid,A.owner_id,time.time()))
    yield s,tid,job,aid


def preview(s,aid):
    with s._postgres.transaction() as c:
        row=c.execute('SELECT * FROM provider_attempts WHERE attempt_id=%s',(aid,)).fetchone()
        job=c.execute('SELECT * FROM worker_jobs WHERE job_id=%s',(row['job_id'],)).fetchone()
        account=c.execute('SELECT * FROM credit_accounts').fetchone()
    return accounting_preview(row,job,account)


def test_atomic_reconciliation_replay_and_explicit_unknown_waiver(failed):
    s,tid,job,aid=failed
    with tenant_context(A):
        p=preview(s,aid)
        out=reconcile(s,aid,review_sha256=p['review_sha256'],original_dump_sha256='a'*64,source_verification=lambda snapshot:True)
        assert out['state']=='settled' and s.worker_job(job)['state']=='failed'
        assert s.provider_attempt(aid)['usage']==p['usage']
        assert reconcile(s,aid,review_sha256=p['review_sha256'],original_dump_sha256='a'*64,source_verification=lambda snapshot:True)['replayed']
        with s._postgres.transaction() as c:
            assert c.execute('SELECT count(*) AS n FROM retail_contributions WHERE job_id=%s',(job,)).fetchone()['n']==1
            assert c.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==p['balance_after']
        assert s.retail_compute_status(tid)=={'unresolved':1}
        w=waive_unknown_compute(s,job+':compute',original_dump_sha256='a'*64,authorization_ref='test-owner-original')
        assert w['state']=='waived' and w['measurement']=='unknown'
        assert waive_unknown_compute(s,job+':compute',original_dump_sha256='a'*64,authorization_ref='test-owner-original')['replayed']
        with s._postgres.transaction() as c:
            row=c.execute('SELECT * FROM retail_compute_segments WHERE job_id=%s',(job,)).fetchone()
            assert row['state']=='waived' and row['deterministic_ms'] is None and row['settled_at'] is None
            assert c.execute('SELECT count(*) AS n FROM retail_contributions WHERE job_id=%s',(job,)).fetchone()['n']==1


def test_changed_review_rollback_and_foreign_denial(failed):
    s,tid,job,aid=failed
    with tenant_context(A):
        with pytest.raises(AdmissionDenied):reconcile(s,aid,review_sha256='bad',original_dump_sha256='a'*64,source_verification=lambda snapshot:True)
        assert s.provider_attempt(aid)['state']=='uncertain'
    with tenant_context(B):
        with pytest.raises(AdmissionDenied):reconcile(s,aid,review_sha256='bad',original_dump_sha256='a'*64,source_verification=lambda snapshot:True)
        with pytest.raises(AdmissionDenied):waive_unknown_compute(s,job+':compute',original_dump_sha256='a'*64,authorization_ref='foreign')


def test_unverified_source_never_settles(failed):
    s,tid,job,aid=failed
    with tenant_context(A):
        p=preview(s,aid)
        with pytest.raises(AdmissionDenied):reconcile(s,aid,review_sha256=p['review_sha256'],original_dump_sha256='a'*64,source_verification=lambda snapshot:False)
        assert s.provider_attempt(aid)['state']=='uncertain'
