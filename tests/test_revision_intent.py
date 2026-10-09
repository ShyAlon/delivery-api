from copy import deepcopy
import hashlib,json
import pytest
from delivery_api.revision_intent import intent_payload
from test_closed_revision_job import options


def proposal():
    return {'proposal_id':'proposal','task_id':'task','session_id':'s','generation':1,
        'source_checkpoint_id':'checkpoint','proposal_checkpoint_id':'new-checkpoint','phase':'srs',
        'change_request':'Describe offline behavior','artifact':{'fake':'typed output'},'artifact_sha256':hashlib.sha256(json.dumps({'fake':'typed output'},sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'typed_and_trace_validated':True,'requires_fresh_review':True,'founder_accepted':False,
        'committed':False,'dispatch_started':False}


def test_intent_is_stable_held_and_does_not_mutate_reviewed_inputs():
    o=options();p=proposal();before=deepcopy((o,p))
    body,digest=intent_payload(options=o,proposal=p,approval_reference='owner-message-ref')
    assert (o,p)==before and len(digest)==64 and body['state']=='held'
    assert not body['generation_installed'] and not body['dispatch_started']
    assert intent_payload(options=o,proposal=p,approval_reference='owner-message-ref')==(body,digest)


@pytest.mark.parametrize('change',[{'phase':'architecture'},{'generation':True},{'committed':True},
    {'source_checkpoint_id':'changed'},{'artifact_sha256':'bad'},{'typed_and_trace_validated':1}])
def test_intent_rejects_wrong_proposal_or_accepted_source(change):
    with pytest.raises(ValueError):intent_payload(options=options(),proposal=proposal()|change,approval_reference='owner-ref')
