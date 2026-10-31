"""Read-only owner preflight against disposable PG, never a live database."""
import pytest
from delivery_api.paid_child_preflight import inspect,SCALE
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.tenancy import tenant_context
from test_attempt_ledger import credit_account,A,DSN


@pytest.fixture
def ready(credit_account):
    import psycopg
    store,_,job=credit_account
    with tenant_context(A):store.finish_worker(job)
    with psycopg.connect(DSN) as c:
        email=c.execute('SELECT email FROM auth.users WHERE id=%s',(A.owner_id,)).fetchone()[0]
        c.execute("INSERT INTO own_test_expense_profiles VALUES(%s,'fixture',%s,%s)",(A.owner_id,2*SCALE,SCALE))
        c.execute("INSERT INTO expense_guard_policy VALUES(true,'fixture',%s,%s,%s,3)",(3*SCALE,3*SCALE,SCALE))
        c.execute('UPDATE credit_accounts SET units_per_credit=1000000000,balance_units=1000000000 WHERE owner_id=%s',(A.owner_id,))
    yield store,email,job
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE worker_jobs SET state='done' WHERE owner_id=%s",(A.owner_id,))
        c.execute('DELETE FROM provider_attempts WHERE owner_id=%s',(A.owner_id,))
        c.execute('DELETE FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,));c.execute('DELETE FROM expense_guard_policy')


def test_read_only_evidence_never_activates_or_writes(ready):
    import psycopg
    _,email,_=ready
    with psycopg.connect(DSN) as c:
        result=inspect(c,email)
        assert result['database_policy_ready'] and result['activation_enabled'] is False and result['unverified']
        assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):c.execute('UPDATE expense_guard_policy SET session_units=1')


@pytest.mark.parametrize('sql,reason',[
    ('DELETE FROM own_test_expense_profiles','own-test profile'),
    ('DELETE FROM expense_guard_policy','policy missing'),
    ('UPDATE expense_guard_policy SET session_units=1','policy missing'),
    ('UPDATE admission_limits SET expense_limit=2','no owner quota'),
    ('UPDATE admission_limits SET concurrency_max=4','exactly three'),
    ('UPDATE credit_accounts SET balance_units=0','positive exact-scale'),
    ("UPDATE worker_jobs SET state='running'",'quiescent'),
])
def test_preflight_refuses_inconsistent_settings_without_repair(ready,sql,reason):
    import psycopg
    _,email,_=ready
    with psycopg.connect(DSN) as c:c.execute(sql)
    with psycopg.connect(DSN) as c,pytest.raises(AdmissionDenied,match=reason):inspect(c,email)


def test_hidden_rows_are_inspection_failure_not_missing(ready):
    import psycopg
    _,email,_=ready
    with psycopg.connect(DSN.replace('user=sandbox','user=delivery_app')) as c,pytest.raises(AdmissionDenied,match='inspection unavailable'):inspect(c,email)


@pytest.mark.parametrize('kind',['provider','compute','history'])
def test_unresolved_or_unscaled_money_is_not_readiness(ready,kind):
    import psycopg,uuid
    store,email,previous_job=ready
    with tenant_context(A):job=store.enqueue_worker(store.worker_job(previous_job)['task_id'],'phase:needs')
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s",(job,))
        if kind=='compute':
            c.execute("INSERT INTO retail_compute_segments(segment_key,owner_id,job_id,phase_key,rates,state,started_at) VALUES(%s,%s,%s,'needs','{}','unresolved',0)",(uuid.uuid4().hex,A.owner_id,job))
        else:
            state='uncertain' if kind=='provider' else 'settled'
            c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,created_at,updated_at,settled_at) VALUES(%s,%s,%s,'test','{\"provider\":\"deepseek\"}','test',%s,0,0,0,0,0,0,0)",(uuid.uuid4().hex,A.owner_id,job,state))
    with psycopg.connect(DSN) as c,pytest.raises(AdmissionDenied,match='accounting|history'):inspect(c,email)
