import pytest
from delivery_api.expense_alert import prepare_alert,delivery_outcome
from delivery_api.attempt_ledger import AdmissionDenied

ARGS=dict(episode_id='episode-1',scope='session',scope_id='session-1',spent_units=3000000,limit_units=3000000,units_per_usd=1000000,occurred_at=1000)


def test_alert_identity_is_episode_scoped_not_rejected_call_scoped():
    first=prepare_alert(**ARGS)
    assert prepare_alert(**{**ARGS,'spent_units':4000000,'occurred_at':2000}).key==first.key
    assert prepare_alert(**{**ARGS,'episode_id':'episode-2'}).key!=first.key
    assert first.payload['running_calls']=='finish' and first.payload['action']=='new_calls_stopped'
    assert not any(key in first.payload for key in ('messages','usage','reasoning_tokens','customer_email'))


def test_unreached_threshold_or_bad_units_never_emits_alert():
    for change in [{'spent_units':2999999},{'units_per_usd':0},{'spent_units':True},{'scope':'user_day'},{'episode_id':''}]:
        with pytest.raises(AdmissionDenied):prepare_alert(**(ARGS|change))


def test_delivery_failures_never_mean_admission_reopened():
    assert delivery_outcome(outcome='accepted',attempts=1,max_attempts=3)=='sent'
    assert delivery_outcome(outcome='transient_failure',attempts=1,max_attempts=3)=='retry_pending'
    for outcome,attempts in [('transient_failure',3),('permanent_failure',1),('unknown',1)]:
        assert delivery_outcome(outcome=outcome,attempts=attempts,max_attempts=3)=='operator_review'
