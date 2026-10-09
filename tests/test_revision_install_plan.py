from copy import deepcopy
import hashlib,json
import pytest
from delivery_api.revision_install_plan import prepare_installation_plan
from delivery_api.revision_install_review import installation_review_payload
from test_revision_install_review import args


def basis():
    a=args();a['lineage']['owner_id']='00000000-0000-0000-0000-000000000001'
    a['lineage_sha256']=hashlib.sha256(json.dumps(a['lineage'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return installation_review_payload(**a)


def test_plan_binds_expected_parent_and_anchor_without_accepting():
    review=basis();before=deepcopy(review)
    result=prepare_installation_plan(**review,installation_id='private-plan')
    assert result==prepare_installation_plan(**review,installation_id='private-plan') and before==review
    plan=result['plan'];anchor=plan['destination_anchor']
    assert anchor['semantic_session_id']=='semantic' and anchor['execution_session_id']=='execution2' and anchor['generation']==2
    assert plan['expected_parent']['execution_session_id']=='s' and plan['expected_parent']['generation']==1
    assert not plan['accepted'] and not plan['installed'] and not plan['dispatch_started'] and not plan['copy_stop_marker']
    assert plan['edited_gate']=={'phase':'srs','state':'decided','approval_reset':False,'approved_by_exact_acceptance':True}


@pytest.mark.parametrize('change',[{'accepted':True},{'installation_available':True},{'copy_stop_marker':True,'preserve_previous_generation':False},
    {'cost_review':{}},{'generation':True},{'generation':3},{'source_snapshot_sha256':'bad'},{'phases_requiring_reapproval':[]}])
def test_plan_rejects_changed_or_effectful_review(change):
    review=basis();body=review['review']|change
    sha=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(ValueError):prepare_installation_plan(review=body,review_sha256=sha,installation_id='private-plan')
