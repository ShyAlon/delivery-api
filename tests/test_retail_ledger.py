import uuid
from decimal import Decimal
import pytest
from test_attempt_ledger import ledger
from test_postgres_store import DSN,A,B,store
from delivery_api.tenancy import tenant_context
from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT
from delivery_api.retail_ledger import post,phase_totals,migrate_account
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
