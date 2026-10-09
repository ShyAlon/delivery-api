import pytest
from delivery_api.revision_install_journal import install_event


def test_install_journal_full_chain_is_immutable_and_never_dispatches():
    previous=install_event(plan_sha256='a'*64,previous=None,state='authorized',evidence={'review_sha256':'b'*64,'acceptance_reference':'constructed-test-ref'})
    for state in ('promotion_pending','files_promoted','install_pending','installed'):
        evidence={'manifest_sha256':'c'*64,'anchor_sha256':'d'*64}
        if state=='installed':evidence['execution_session_id']='next'
        result=install_event(plan_sha256='a'*64,previous=previous,state=state,evidence=evidence)
        assert result['event']['previous_sha256']==previous['event_sha256'] and not result['event']['dispatch_started']
        previous=result
    with pytest.raises(ValueError):install_event(plan_sha256='a'*64,previous=previous,state='promotion_pending',evidence=evidence)


def test_install_journal_changed_anchor_and_skipped_state_reject():
    first=install_event(plan_sha256='a'*64,previous=None,state='authorized',evidence={'review_sha256':'b'*64,'acceptance_reference':'test-ref'})
    ev={'manifest_sha256':'c'*64,'anchor_sha256':'d'*64}
    with pytest.raises(ValueError):install_event(plan_sha256='a'*64,previous=first,state='installed',evidence=ev|{'execution_session_id':'next'})
    pending=install_event(plan_sha256='a'*64,previous=first,state='promotion_pending',evidence=ev)
    with pytest.raises(ValueError,match='changed across journal'):install_event(plan_sha256='a'*64,previous=pending,state='files_promoted',evidence=ev|{'anchor_sha256':'e'*64})
    held=install_event(plan_sha256='a'*64,previous=pending,state='held_for_review',evidence={'reason':'ambiguous rename, no automatic retry'})
    assert not held['event']['automatic_replay_allowed']
