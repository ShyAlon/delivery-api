"""Provider/prepaid/outbox queries are native while exact billing fences stay real."""
import ast,inspect,textwrap
from uuid import uuid4
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied

METHODS=('staged_response_ledger_join','provider_attempt','enqueue_expense_alert','claim_expense_alert',
 'finish_expense_alert','recover_uncertain_expense_alert','record_raw_provider_usage',
 'confirmed_expense_admission','start_launch_provider_attempt','record_provider_balance_check')


def test_provider_prepaid_queries_do_not_consume_bridge():
    from delivery_api import attempt_ledger,prepaid
    from delivery_api.postgres_store import PostgresStore
    for source in [inspect.getsource(attempt_ledger),inspect.getsource(prepaid)]+[textwrap.dedent(inspect.getsource(inspect.unwrap(getattr(PostgresStore,n)))) for n in METHODS]:
        assert not any(isinstance(n,ast.Attribute) and n.attr=='_db' for n in ast.walk(ast.parse(source)))


def test_native_prepaid_settlement_outbox_and_owner_fences(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    from delivery_api.expense_alert import prepare_alert
    from test_attempt_ledger import Q
    with pg_workspace_factory() as w,pg_workspace_factory() as other:
        s=w.store;aid=uuid4().hex;quote=Q|{'retail_units_per_credit':1000000}
        with w.admin() as c:
            c.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2)',(w.scope.owner_id,'test-only'))
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES (%s,1,1000000,0)',(w.scope.owner_id,))
        with tenant_context(w.scope):
            tid=s.create_task('Native prepaid')['task_id'];s.set_engine_session(tid,'prepaid-'+uuid4().hex)
            job=s.enqueue_worker(tid,'phase:needs');assert s.claim_worker(job)
        execute=_Connection.execute
        def reject(self,sql,parameters=()):
            if any(t in sql for t in ('provider_attempts','credit_accounts','credit_charges','admission_limits','expense_alert_outbox','requi_confirmed_expense_guard','staged_provider_responses','pg_advisory_xact_lock','requi.ledger_')):
                raise AssertionError('provider/prepaid query still consumes bridge')
            return execute(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            with tenant_context(w.scope):
                s.reserve_provider_attempt(job,aid,quote,'native')
                s.transition_provider_attempt(aid,'start',require_prepaid=True)
                assert s.record_raw_provider_usage(aid,{'raw':'fixture'})['raw_usage']=={'raw':'fixture'}
                usage={'input_tokens':2,'output_tokens':3}
                settled=s.transition_provider_attempt(aid,'settle',usage,require_prepaid=True)
                assert settled['retail_actual']==18
                assert s.transition_provider_attempt(aid,'settle',usage,require_prepaid=True)['state']=='settled'
                with s._postgres.transaction() as c:
                    assert c.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==-17
                    assert c.execute("SELECT count(*) AS n FROM credit_ledger WHERE kind='debit'").fetchone()['n']==1
                with pytest.raises(AdmissionDenied):s.transition_provider_attempt(aid,'settle',{'input_tokens':1,'output_tokens':1},require_prepaid=True)
                nxt=uuid4().hex;s.reserve_provider_attempt(job,nxt,quote,'next')
                with pytest.raises(AdmissionDenied,match='positive prepaid'):s.transition_provider_attempt(nxt,'start',require_prepaid=True)
                assert s.provider_attempt(nxt)['state']=='reserved'
                s.transition_provider_attempt(nxt,'cancel')
                assert s.staged_response_ledger_join('prepaid-missing',1)==[]
                assert s.confirmed_expense_admission(job)=='expense_policy_missing'
                alert=prepare_alert(episode_id=uuid4().hex,scope='session',scope_id='fake',spent_units=3,limit_units=3,units_per_usd=1,occurred_at=1)
                first=s.enqueue_expense_alert(alert,max_attempts=2)
                assert s.enqueue_expense_alert(alert,max_attempts=2)==first
                assert s.claim_expense_alert(alert.key)['attempts']==1
                assert s.finish_expense_alert(alert.key,'transient_failure')=='retry_pending'
                assert s.claim_expense_alert(alert.key)['attempts']==2
                s.recover_uncertain_expense_alert(alert.key)
                assert s.claim_expense_alert(alert.key) is None
            with tenant_context(other.scope):
                assert s.provider_attempt(aid) is None
                with pytest.raises(AdmissionDenied):s.transition_provider_attempt(aid,'settle',usage)
                with pytest.raises(KeyError):s.claim_expense_alert(alert.key)
