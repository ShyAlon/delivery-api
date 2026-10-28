"""Native retail/account query paths keep exact remainder and owner-only views."""
from decimal import Decimal
from uuid import uuid4
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied


def test_native_retail_account_queries(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store
        with a.admin() as c:c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES (%s,1,%s,0)',(a.scope.owner_id,UNITS_PER_CREDIT))
        with tenant_context(a.scope):
            tid=s.create_task('Native retail')['task_id'];s.set_engine_session(tid,'retail-'+uuid4().hex)
            job=s.enqueue_worker(tid,'phase:needs');assert s.claim_worker(job)
        execute=_Connection.execute
        def reject(self,sql,parameters=()):
            if any(t in sql for t in ('retail_contributions','retail_compute_segments','credit_accounts','credit_ledger','provider_attempts','requi.ledger_')):
                raise AssertionError('retail/account still consumes query bridge')
            return execute(self,sql,parameters)
        rates=RetailRates.from_environment({}).snapshot()
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            with tenant_context(a.scope):
                for i in range(100):s.post_retail_contribution(key=f'{job}:{i}',job_id=job,phase='needs',kind='compute',deterministic_ms='0.0003',rates=rates)
                s.post_retail_contribution(key=f'{job}:0',job_id=job,phase='needs',kind='compute',deterministic_ms='0.0003',rates=rates)
                with s._postgres.transaction() as c:
                    row=c.execute('SELECT * FROM credit_accounts').fetchone()
                    assert row['balance_units']==0 and row['fractional_debit_units']==Decimal('.5')
                assert s.retail_phase_totals(job)['needs']['credits']==Decimal('0.0000000015')
                assert s.task_retail_totals(tid) is not None
                assert s.task_phase_credits(tid)
                s.begin_retail_compute('native-segment',job,'needs',rates)
                assert s.retail_compute_status(tid)=={'pending':1}
                s.finish_retail_compute('native-segment',0)
                assert s.retail_compute_status(tid)=={'settled':1}
                assert s.own_credit_balance(1)=={'credits':0.0,'usd':0.0}
                assert s.own_credit_history()['available']
                assert s.own_last_topup() is None
                assert s.own_ledger(1)['text']
                with pytest.raises(AdmissionDenied):s.post_retail_contribution(key=f'{job}:0',job_id=job,phase='needs',kind='compute',deterministic_ms=1,rates=rates)
            with tenant_context(b.scope):
                assert s.retail_phase_totals(job)=={}
                assert s.task_retail_totals(tid) is None
                assert s.retail_compute_status(tid)=={}
                assert s.own_credit_balance(1)=={'credits':0.0,'usd':0.0}
                assert s.own_credit_history()['entries']==[]
                with pytest.raises(AdmissionDenied):s.post_retail_contribution(key='foreign',job_id=job,phase='needs',kind='compute',deterministic_ms=1,rates=rates)
