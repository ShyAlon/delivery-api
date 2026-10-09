from copy import deepcopy
import pytest
from delivery_api.revision_journal import journal_event,reconcile_promotion


def chain():
    first=journal_event(lineage_sha256='a'*64,previous=None,state='prepared',evidence={'intent_id':'intent','lineage_id':'lineage'})
    staged=journal_event(lineage_sha256='a'*64,previous=first,state='staged',evidence={'manifest_sha256':'b'*64,'checkpoint_id':'checkpoint'})
    return first,staged


def test_digest_chain_stable_and_no_implicit_dispatch():
    first,staged=chain();before=deepcopy(staged)
    event=journal_event(lineage_sha256='a'*64,previous=staged,state='promotion_pending',evidence={'manifest_sha256':'b'*64})
    assert event['event']['previous_sha256']==staged['event_sha256'] and event['event']['sequence']==2
    assert not event['event']['dispatch_started'] and staged==before


@pytest.mark.parametrize('state',['files_promoted','install_pending','installed','prepared'])
def test_skipping_publication_or_review_stage_is_rejected(state):
    first,staged=chain()
    with pytest.raises(ValueError,match='transition'):
        journal_event(lineage_sha256='a'*64,previous=staged,state=state,evidence={})


def test_tampering_or_manifest_change_is_rejected():
    first,staged=chain();staged['event']['state']='promotion_pending'
    with pytest.raises(ValueError,match='prior'):
        journal_event(lineage_sha256='a'*64,previous=staged,state='files_promoted',evidence={'manifest_sha256':'b'*64})
    first,staged=chain()
    with pytest.raises(ValueError,match='manifest changed'):
        journal_event(lineage_sha256='a'*64,previous=staged,state='promotion_pending',evidence={'manifest_sha256':'c'*64})


def test_held_review_is_terminal_not_auto_replay():
    first,staged=chain()
    held=journal_event(lineage_sha256='a'*64,previous=staged,state='held_for_review',evidence={'reason':'interrupted'})
    with pytest.raises(ValueError,match='transition'):
        journal_event(lineage_sha256='a'*64,previous=held,state='promotion_pending',evidence={'manifest_sha256':'b'*64})


@pytest.mark.parametrize('files,db',[(False,False),(True,False),(False,True),(True,True)])
def test_reconciliation_never_retries_deletes_or_dispatches(files,db):
    result=reconcile_promotion(state='install_pending',files_match_manifest=files,database_matches_installation=db)
    assert not result['automatic_retry'] and not result['automatic_cleanup'] and not result['dispatch_started']


def test_installed_verified_is_not_permission_to_dispatch():
    result=reconcile_promotion(state='installed',files_match_manifest=True,database_matches_installation=True)
    assert result['state']=='installed_verified_no_dispatch' and not result['dispatch_started']
