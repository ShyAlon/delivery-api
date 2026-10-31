"""Operator installation preview/apply/rollback in disposable PG, no live changes."""
import pytest
from delivery_api.confirmed_policy_install import plan
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.tenancy import tenant_context
from test_attempt_ledger import credit_account,A,DSN


@pytest.fixture
def quiet(credit_account):
    store,_,job=credit_account
    with tenant_context(A):store.finish_worker(job)
    return store,job


def test_preview_commit_replay_and_rollback_only_change_policy(quiet):
    import psycopg
    with psycopg.connect(DSN) as c:
        before=c.execute('SELECT * FROM admission_limits').fetchall()
        assert not plan(c)['already_installed']
        assert c.execute('SELECT * FROM expense_guard_policy').fetchall()==[]
        assert plan(c,apply=True)['activation_enabled'] is False
        assert plan(c,apply=True)['already_installed']
        assert c.execute('SELECT * FROM admission_limits').fetchall()==before
        assert c.execute('SELECT * FROM own_test_expense_profiles').fetchall()==[]
        c.rollback()
    with psycopg.connect(DSN) as c:assert c.execute('SELECT * FROM expense_guard_policy').fetchall()==[]


@pytest.mark.parametrize('sql,reason',[
    ("UPDATE worker_jobs SET state='running'",'quiescent'),
    ("INSERT INTO expense_guard_policy VALUES(true,'different',1,1,1,1)",'differs'),
])
def test_install_refuses_existing_conflict_or_live_work(quiet,sql,reason):
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute(sql)
        with pytest.raises(AdmissionDenied,match=reason):plan(c,apply=True)
        c.rollback()


def test_script_requires_both_operator_acknowledgments_and_never_launches():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/ops/confirmed-expense-policy.sh').read_text()
    assert "ack!='reviewed runtime includes production launch guard'" in text and "ack!='yes'" in text
    assert text.index('plan(conn),')<text.index('plan(conn,apply=True)')
    for unsafe in ('docker run','start-next-phase','DELETE FROM','UPDATE admission_limits','API_KEY'):assert unsafe not in text


def test_hidden_accounting_is_not_quiescent(quiet):
    import psycopg
    with psycopg.connect(DSN.replace('user=sandbox','user=delivery_app')) as c,pytest.raises(AdmissionDenied,match='inspection unavailable'):plan(c,apply=True)
