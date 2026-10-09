import pytest
from delivery_api.expense_alarm_policy import from_environment,threshold_crossed
from delivery_api.attempt_ledger import AdmissionDenied

ENV={'VMODEL_EXPENSE_UNITS_PER_USD':'1000000','VMODEL_SESSION_EXPENSE_LIMIT_USD':'3',
     'VMODEL_DAILY_EXPENSE_PER_REGISTERED_USER_USD':'3','VMODEL_OVERALL_EXPENSE_SCOPE':'platform_registered_users',
     'VMODEL_EXPENSE_DAY_MODE':'rolling_24h'}


def test_exact_expense_config_and_threshold_are_separate_from_retail():
    policy=from_environment(ENV)
    assert policy.session_units==3000000 and policy.daily_per_registered_user_units==3000000
    assert not threshold_crossed(2999999,policy.session_units)
    assert threshold_crossed(3000000,policy.session_units)
    assert threshold_crossed(3000001,policy.session_units)


@pytest.mark.parametrize('key',list(ENV))
def test_no_implicit_scope_scale_day_or_limit(key):
    env=dict(ENV);env.pop(key)
    with pytest.raises(AdmissionDenied):from_environment(env)


@pytest.mark.parametrize('value',['NaN','Infinity','-3','0','0.0000001','broken'])
def test_invalid_or_nonrepresentable_money_denied(value):
    with pytest.raises(AdmissionDenied):from_environment(ENV | {'VMODEL_SESSION_EXPENSE_LIMIT_USD':value})


def test_invalid_amounts_are_not_float_rounded():
    for value in [-1,True,1.5,None]:
        with pytest.raises(AdmissionDenied):threshold_crossed(value,3000000)


def test_platform_ceiling_scales_with_verified_count_not_owner_daily_quota():
    from delivery_api.expense_alarm_policy import platform_daily_limit
    policy=from_environment(ENV)
    assert platform_daily_limit(policy,0)==0
    assert platform_daily_limit(policy,10)==30000000
    for value in [True,-1,2.5,'10',None]:
        with pytest.raises(AdmissionDenied):platform_daily_limit(policy,value)


def test_rolling_window_uses_settlement_time_and_never_assumes_unsettled_zero():
    from delivery_api.expense_alarm_policy import rolling_expense
    now=100000
    rows=[{'state':'settled','settled_at':now-86400,'expense_actual':8},
          {'state':'settled','settled_at':now-86399,'expense_actual':2},
          {'state':'settled','settled_at':now,'expense_actual':3},
          {'state':'cancelled'}]
    assert rolling_expense(rows,now)==5
    with pytest.raises(AdmissionDenied):rolling_expense(rows+[{'state':'uncertain'}],now)
