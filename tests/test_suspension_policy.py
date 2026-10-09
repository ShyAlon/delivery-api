import pytest
from delivery_api.suspension_policy import action_allowed,suspension_proposal,TASK_OPERATIONS,PRESERVED_ACTIONS
ACCOUNT={'owner_id':'00000000-0000-0000-0000-000000000002','suspended':False,'version':0}

@pytest.mark.parametrize('action',sorted(TASK_OPERATIONS))
def test_suspended_blocks_each_task_operation(action):
    assert not action_allowed(suspended=True,action=action)
    assert action_allowed(suspended=False,action=action)

@pytest.mark.parametrize('action',sorted(PRESERVED_ACTIONS))
def test_preserves_communication_reads_exports_and_accepted_call_settlement(action):
    assert action_allowed(suspended=True,action=action)
    assert action_allowed(suspended=False,action=action)

@pytest.mark.parametrize('state',[None,0,1,'false'])
def test_state_must_be_verified_boolean(state):
    with pytest.raises(ValueError):action_allowed(suspended=state,action='task.read')

def test_unknown_actions_not_silently_authorized():
    for state in (True,False):
        with pytest.raises(ValueError):action_allowed(suspended=state,action='POST')

def test_review_binds_target_status_version_reason_without_effect():
    before=ACCOUNT.copy()
    p=suspension_proposal(account=ACCOUNT,suspend=True,reason='Reviewed test')
    assert ACCOUNT==before and not p['effect_executed'] and not p['commit_available']
    assert p==suspension_proposal(account=ACCOUNT,suspend=True,reason='Reviewed test')
    for account,reason in ((ACCOUNT|{'version':1},'Reviewed test'),
        (ACCOUNT|{'owner_id':'00000000-0000-0000-0000-000000000003'},'Reviewed test'),(ACCOUNT,'Different reason')):
        assert suspension_proposal(account=account,suspend=True,reason=reason)['review_digest']!=p['review_digest']
    assert not suspension_proposal(account=ACCOUNT|{'suspended':True},suspend=False,reason='Restore access')['after_suspended']

@pytest.mark.parametrize('change',[{'owner_id':'bad'},{'suspended':1},{'version':True},{'version':-1},{'version':1.0}])
def test_bad_snapshots_rejected(change):
    with pytest.raises(ValueError):suspension_proposal(account=ACCOUNT|change,suspend=True,reason='test')

def test_noops_and_invalid_reasons_rejected():
    with pytest.raises(ValueError):suspension_proposal(account=ACCOUNT,suspend=False,reason='test')
    for reason in ('','   ','x'*501,'new\nline'):
        with pytest.raises(ValueError):suspension_proposal(account=ACCOUNT,suspend=True,reason=reason)
