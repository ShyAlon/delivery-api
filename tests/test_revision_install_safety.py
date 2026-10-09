import pytest
from delivery_api.revision_install_safety import installation_safety_snapshot


def test_quiescence_evidence_is_not_install_authority():
    result=installation_safety_snapshot(parent_current=True,source_writer_excluded=True,unresolved_jobs=[],unresolved_attempts=[])
    assert result['observed_work_quiescent'] and not result['installation_available'] and not result['accepted']
    assert not result['automatic_replay_allowed'] and not result['dispatch_started']


def test_unsettled_call_stays_blocking_after_job_finished():
    result=installation_safety_snapshot(parent_current=True,source_writer_excluded=True,unresolved_jobs=[],unresolved_attempts=['call'])
    assert result['blockers']==['provider_attempts_unresolved'] and not result['observed_work_quiescent']


def test_all_unresolved_safety_checks_retained():
    result=installation_safety_snapshot(parent_current=False,source_writer_excluded=False,unresolved_jobs=['job','job'],unresolved_attempts=['call'])
    assert len(result['blockers'])==4 and result['unresolved_job_ids']==['job']


@pytest.mark.parametrize('value',[None,1,'yes'])
def test_quiescence_boolean_is_exact(value):
    with pytest.raises(ValueError):installation_safety_snapshot(parent_current=True,source_writer_excluded=value,unresolved_jobs=[],unresolved_attempts=[])
