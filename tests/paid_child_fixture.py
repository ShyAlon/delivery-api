"""Explicit fake-provider paid-child acceptance setup in disposable PG only."""
import hashlib
from contextlib import contextmanager
from delivery_api.child_paid_runtime import PaidChildRuntime
from delivery_api.producer_admission import WorkerAdmissionGateway
from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT
from delivery_api.deepseek_usage import settlement_usage
from test_postgres_store import DSN,A


@contextmanager
def paid_child(store,endpoint):
    import psycopg
    rates=RetailRates.from_environment({})
    quote={'provider':'deepseek','model':'deepseek-flash','price_version':'test','bound_version':'test','input_max':10000,
        'expense_input':1,'expense_output':1,'expense_cache_hit':1,'expense_cache_miss':1,'expense_units_per_usd':1,
        'retail_input':12500,'retail_output':12500,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_formula':rates.snapshot()}
    with psycopg.connect(DSN) as conn:
        conn.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,100000000000,%s,0)',(A.owner_id,UNITS_PER_CREDIT))
        conn.execute('INSERT INTO admission_limits VALUES(%s,%s,86400,NULL,NULL,NULL,NULL,60,NULL,3)',(A.owner_id,'test'))
        conn.execute("INSERT INTO expense_guard_policy VALUES(true,'test-only',1000000,1000000,1,3)")
    def factory(cap,job):
        def resolve(**kw):return quote|{'output_max':kw['max_output_tokens'],'retail_phase':kw['stage']},hashlib.sha256(kw['serialized_request']).hexdigest()
        return WorkerAdmissionGateway(store,job,resolve_serialized=resolve,measure_raw_usage=settlement_usage,launch_guard=True,retail_rates=rates)
    class FixtureRuntime(PaidChildRuntime):
        def transport(self,*args):return endpoint,{}
    try:yield factory,FixtureRuntime(models={'deepseek':'deepseek-flash'},api_key='fixture-only')
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute('DELETE FROM expense_alert_outbox');conn.execute('DELETE FROM expense_guard_episodes');conn.execute('DELETE FROM expense_guard_policy')
            for table in ('retail_compute_segments','retail_contributions','credit_charges','provider_attempts','credit_accounts','admission_limits'):
                conn.execute('DELETE FROM '+table+' WHERE owner_id=%s',(A.owner_id,))
