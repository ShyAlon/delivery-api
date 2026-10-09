import pytest
from delivery_api.expense_alarm_policy import ExpenseAlarmPolicy
from delivery_api.operator_expense_snapshot import build_snapshot
from delivery_api.attempt_ledger import AdmissionDenied
P=ExpenseAlarmPolicy(300,300,100,'platform_registered_users','rolling_24h')


def data():
    return dict(accounts=[{'owner_id':'a','registered':True,'deleted_at':None},{'owner_id':'b','registered':True,'deleted_at':10}],
        attempts=[{'attempt_id':'old','owner_id':'b','session_id':'s','state':'settled','settled_at':100,'expense_actual':20},
                  {'attempt_id':'new','owner_id':'a','session_id':'s','state':'settled','settled_at':90000,'expense_actual':80},
                  {'attempt_id':'held','owner_id':'b','session_id':'s','state':'uncertain','expense_reserved':250}],
        as_of=100000,registry_complete=True,ledger_complete=True,policy=P)


def test_registry_count_excludes_deleted_but_cost_and_holds_preserved():
    got=build_snapshot(**data())
    assert got['registered_non_deleted_accounts']==1 and got['platform_limit_units']==300
    assert got['platform_rolling_settled_units']==80 and got['platform_outstanding_hold_units']==250
    assert got['platform_new_calls_blocked'] and got['operator_only'] and not got['installed']
    deleted_session=next(s for s in got['sessions'] if s['owner_id']=='b')
    assert deleted_session['settled_units']==20 and deleted_session['held_units']==250


@pytest.mark.parametrize('change',[{'registry_complete':False},{'ledger_complete':False},
    {'accounts':[{'owner_id':'a','registered':True}]},
    {'attempts':[{'attempt_id':'u','owner_id':'a','session_id':'s','state':'uncertain'}]}])
def test_incomplete_or_uncertain_without_hold_never_assumes_zero(change):
    with pytest.raises(AdmissionDenied):build_snapshot(**(data()|change))


def test_duplicates_and_missing_account_are_rejected():
    kw=data()
    for rows in [kw['attempts']+[kw['attempts'][0]],[kw['attempts'][0]|{'owner_id':'unknown'}]]:
        with pytest.raises(AdmissionDenied):build_snapshot(**(kw|{'attempts':rows}))
