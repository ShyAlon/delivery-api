import uuid
from decimal import Decimal
import pytest
from test_attempt_ledger import ledger
from test_postgres_store import DSN, A, B
from delivery_api.tenancy import tenant_context
from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT
from delivery_api.retail_ledger import migrate_account
from delivery_api.attempt_ledger import AdmissionDenied
pytestmark=pytest.mark.skipif(not DSN,reason='disposable migrated Postgres required')

@pytest.fixture
def account(ledger):
    import psycopg
    s,tid,job=ledger
    with psycopg.connect(DSN) as c:
        c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1,1000000000,0)',(A.owner_id,))
    yield s,tid,job
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM retail_compute_segments WHERE job_id=%s',(job,))
        c.execute('DELETE FROM retail_contributions WHERE job_id=%s',(job,))
        c.execute('DELETE FROM credit_charges WHERE owner_id=%s',(A.owner_id,))
        c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))
        c.execute('DELETE FROM retail_scale_migrations WHERE owner_id=%s',(A.owner_id,))


def test_exact_token_debit_once_and_phase_rollup(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        q={'provider':'fake','model':'fake','price_version':'fixture','bound_version':'fixture','input_max':10,'output_max':10,'expense_input':1,'expense_output':2,'retail_input':12500,'retail_output':12500,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_formula':r}
        attempt=s.reserve_provider_attempt(job,'fine-'+uuid.uuid4().hex,q,'digest',require_prepaid=True)['attempt_id']
        s.transition_provider_attempt(attempt,'start',require_prepaid=True)
        u={'input_tokens':2,'output_tokens':3}
        assert s.transition_provider_attempt(attempt,'settle',u)['retail_actual']==62500
        s.transition_provider_attempt(attempt,'settle',u)
        with s._postgres.transaction() as c:assert c.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==1-62500
        assert s.retail_phase_totals(job)['needs']['credits']==Decimal('0.0000625')


def test_compute_remainder_and_replay_are_durable(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        for i in range(100):s.post_retail_contribution(key=f'{job}:{i}',job_id=job,phase='needs',kind='compute',deterministic_ms='0.0003',rates=r)
        s.post_retail_contribution(key=f'{job}:0',job_id=job,phase='needs',kind='compute',deterministic_ms='0.0003',rates=r)
        with s._postgres.transaction() as c:a=c.execute('SELECT * FROM credit_accounts').fetchone()
        assert a['balance_units']==0 and a['fractional_debit_units']==Decimal('.5')
        assert s.retail_phase_totals(job)['needs']['credits']==Decimal('0.0000000015')
        with pytest.raises(AdmissionDenied):s.post_retail_contribution(key=f'{job}:0',job_id=job,phase='needs',kind='compute',deterministic_ms=1,rates=r)
    with tenant_context(B):
        assert not s.retail_phase_totals(job)
        with pytest.raises(AdmissionDenied):s.post_retail_contribution(key='foreign',job_id=job,phase='needs',kind='compute',deterministic_ms=1,rates=r)


def test_account_scale_migration_preserves_value_and_replay(account):
    import psycopg
    from psycopg.rows import dict_row
    s,tid,job=account
    with psycopg.connect(DSN,row_factory=dict_row) as c:
        c.execute('UPDATE credit_accounts SET balance_units=-17,units_per_credit=1000 WHERE owner_id=%s',(A.owner_id,))
        assert migrate_account(c,A.owner_id,now=1)
        assert not migrate_account(c,A.owner_id,now=2)
        row=c.execute('SELECT * FROM credit_accounts WHERE owner_id=%s',(A.owner_id,)).fetchone()
        assert row['balance_units']==-17000000 and row['units_per_credit']==UNITS_PER_CREDIT
        assert c.execute('SELECT count(*) AS n FROM retail_scale_migrations').fetchone()['n']==1


def test_compute_segment_settles_once_and_failed_measurement_holds(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        s.begin_retail_compute(job,job,'needs',r)
        s.finish_retail_compute(job,'10')
        s.finish_retail_compute(job,'10')
        assert s.retail_phase_totals(job)['needs']['credits']==Decimal('0.0000005')
        with pytest.raises(AdmissionDenied):s.finish_retail_compute(job,'11')
        s.begin_retail_compute(job+'failed',job,'needs',r)
        s.finish_retail_compute(job+'failed',None)
        with pytest.raises(AdmissionDenied):s.finish_retail_compute(job+'failed','1')


def test_browser_roles_cannot_read_retail_evidence(account):
    s,tid,job=account
    with tenant_context(A),s._postgres.transaction() as c:
        for name in ('retail_contributions','retail_compute_segments','retail_scale_migrations'):
            with pytest.raises(Exception),c.transaction():
                c.execute('SET LOCAL ROLE authenticated')
                c.execute('SELECT * FROM '+name)


def test_provider_ceiling_is_not_confirmed_expense_and_boundary_is_held(account):
    from test_provider_snapshot import fixture
    from delivery_api.provider_snapshot import ProviderSnapshot
    from datetime import datetime,timezone
    import psycopg
    s,tid,job=account;data=fixture();now=datetime.now(timezone.utc);data['fetched_at']=now.isoformat()
    with psycopg.connect(DSN) as c:c.execute('UPDATE admission_limits SET expense_limit=NULL,attempt_expense_max=NULL WHERE owner_id=%s',(A.owner_id,))
    snapshot=ProviderSnapshot(data);rates=RetailRates.from_environment({}).snapshot()
    q={'provider':'fake','model':'fixture','price_version':'fixture','bound_version':'fixture','input_max':10,'output_max':10,'expense_input':300000,'expense_cache_hit':6000,'expense_cache_miss':300000,'expense_output':1200000,'expense_units_per_usd':10**12,'retail_input':12500,'retail_output':12500,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_formula':rates,'provider_evidence':snapshot.quote_evidence(now)}
    with tenant_context(A):
        attempt=s.reserve_provider_attempt(job,uuid.uuid4().hex,q,'one',require_prepaid=True)['attempt_id']
        s.transition_provider_attempt(attempt,'start',require_prepaid=True)
        u={'input_tokens':2,'output_tokens':3,'cache_hit_tokens':1,'cache_miss_tokens':1}
        result=s.transition_provider_attempt(attempt,'settle',u)
        assert result['expense_actual']==3000+150000+3*600000
        assert result['billed_rate_evidence']['band']=='off_peak'
        assert result['quote']['expense_output']==1200000
        assert s.transition_provider_attempt(attempt,'settle',u)['expense_actual']==result['expense_actual']
        # Deliberately unverifiable interval remains unknown, not a peak charge.
        second=s.reserve_provider_attempt(job,uuid.uuid4().hex,q,'two',require_prepaid=True)['attempt_id']
    with psycopg.connect(DSN) as c:c.execute('UPDATE credit_accounts SET balance_units=1 WHERE owner_id=%s',(A.owner_id,))
    with tenant_context(A):s.transition_provider_attempt(second,'start',require_prepaid=True)
    with psycopg.connect(DSN) as c:c.execute('UPDATE provider_attempts SET provider_started_at=%s WHERE attempt_id=%s',(now.timestamp()-90000,second))
    with tenant_context(A):
        result=s.transition_provider_attempt(second,'settle',u)
        assert result['state']=='uncertain' and result['expense_actual'] is None
        with s._postgres.transaction() as c:assert c.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==1


def test_pending_attempt_refuses_scale_migration_without_history_rewrite(account):
    import psycopg
    from psycopg.rows import dict_row
    from test_attempt_ledger import Q
    s,tid,job=account
    with tenant_context(A):s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'pending')
    with psycopg.connect(DSN,row_factory=dict_row) as c:
        c.execute('UPDATE credit_accounts SET units_per_credit=1000,balance_units=5000 WHERE owner_id=%s',(A.owner_id,))
        with pytest.raises(AdmissionDenied,match='pending'):
            migrate_account(c,A.owner_id,now=1)
        assert c.execute('SELECT units_per_credit FROM credit_accounts WHERE owner_id=%s',(A.owner_id,)).fetchone()['units_per_credit']==1000


def test_parallel_subunit_posting_serializes_owner_remainder(account):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    def one(i):
        db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):return db.post_retail_contribution(key=job+':parallel:'+str(i),job_id=job,phase='needs',kind='compute',deterministic_ms='0.01',rates=r)['posted_units']
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(one,range(20)))
    assert sum(results)==10
    with tenant_context(A),s._postgres.transaction() as c:
        row=c.execute('SELECT balance_units,fractional_debit_units FROM credit_accounts WHERE owner_id=%s',(A.owner_id,)).fetchone()
        assert row['balance_units']==-9 and row['fractional_debit_units']==0


def test_failed_contribution_does_not_debit_or_drop_remainder(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        with pytest.raises(Exception):
            s.post_retail_contribution(key=job+':bad',job_id=job,phase=None,kind='compute',deterministic_ms='0.01',rates=r)
        with s._postgres.transaction() as c:
            row=c.execute('SELECT balance_units,fractional_debit_units FROM credit_accounts').fetchone()
            assert row['balance_units']==1 and row['fractional_debit_units']==0
            assert c.execute('SELECT count(*) AS n FROM retail_contributions').fetchone()['n']==0


def test_compute_allocation_uses_call_phase_and_preserves_total(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        for phase in ('needs','srs','srs'):
            s.post_retail_contribution(key=job+uuid.uuid4().hex,job_id=job,phase=phase,kind='tokens',input_tokens=1,rates=r)
        s.begin_retail_compute(job,job,'needs',r);s.finish_retail_compute(job,30)
        totals=s.retail_phase_totals(job)
        assert totals['needs']['credits']==Decimal('0.000013')
        assert totals['srs']['credits']==Decimal('0.000026')
        assert sum(x['credits'] for x in totals.values())==Decimal('0.000039')


def test_estimate_overrun_bills_actual_once_and_holds_new_calls(account):
    from test_provider_snapshot import fixture
    from delivery_api.provider_snapshot import ProviderSnapshot
    from datetime import datetime,timezone
    import psycopg
    s,tid,job=account;d=fixture();now=datetime.now(timezone.utc);d['fetched_at']=now.isoformat();v=ProviderSnapshot(d)
    with psycopg.connect(DSN) as c:c.execute('UPDATE admission_limits SET expense_limit=NULL,attempt_expense_max=NULL WHERE owner_id=%s',(A.owner_id,))
    q={'provider':'fake','model':'fixture','price_version':'fixture','bound_version':'fixture','input_max':1,'output_max':10,'expense_input':300000,'expense_cache_hit':6000,'expense_cache_miss':300000,'expense_output':1200000,'expense_units_per_usd':10**12,'retail_input':12500,'retail_output':12500,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_formula':RetailRates.from_environment({}).snapshot(),'provider_evidence':v.quote_evidence(now),'estimate_policy':{'kind':'complete-wire-bytes-plus4096-v1','ceiling':128000,'input_estimate':1,'hard_output_cap':10,'verified_input_bound':False}}
    with tenant_context(A):
        a=s.reserve_provider_attempt(job,uuid.uuid4().hex,q,'estimate',require_prepaid=True)['attempt_id']
        s.record_provider_balance_check(a,{'response':{'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'1'}]},'observed_at':now.isoformat()})
        s.transition_provider_attempt(a,'start',require_prepaid=True)
        u={'input_tokens':3,'output_tokens':2,'cache_hit_tokens':0,'cache_miss_tokens':3}
        result=s.transition_provider_attempt(a,'settle',u)
        assert result['state']=='settled' and result['input_estimate_overrun'] is True and result['retail_actual']==62500
        assert s.transition_provider_attempt(a,'settle',u)['retail_actual']==62500
        with pytest.raises(AdmissionDenied,match='estimate'):s.reserve_provider_attempt(job,uuid.uuid4().hex,q,'later',require_prepaid=True)
