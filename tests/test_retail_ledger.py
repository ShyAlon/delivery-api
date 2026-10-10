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
