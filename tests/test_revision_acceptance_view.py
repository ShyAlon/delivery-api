import hashlib,json
from pathlib import Path
import pytest
from delivery_api.revision_acceptance_view import revision_acceptance_view
from delivery_api.revision_install_review import installation_review_payload
from test_revision_install_review import args


def basis():
    artifact=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())['srs']
    a=args();a['lineage']['semantic_session_id']='s'
    a['lineage']['regenerated_artifact_sha256']=hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    a['lineage_sha256']=hashlib.sha256(json.dumps(a['lineage'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return artifact,installation_review_payload(**a)


def test_view_uses_exact_typed_artifact_with_no_visible_raw_ids_or_json():
    artifact,review=basis();result=revision_acceptance_view(**review,artifact=artifact,spent_credits=4.2)
    assert result['binding']['review_sha256']==review['review_sha256']
    view=result['view'];assert view['phase_title']=='Requirements' and view['default_choice']=='approve' and view['artifact_rows']
    assert view['requires_extra_cost_acknowledgement'] and not view['installation_available']
    assert not any('requirement_id' in row or 'session_id' in row for row in view['artifact_rows'])


@pytest.mark.parametrize('amount',[True,-1,float('inf'),float('nan')])
def test_bad_current_cost_rejected(amount):
    artifact,review=basis()
    with pytest.raises(ValueError):revision_acceptance_view(**review,artifact=artifact,spent_credits=amount)


def test_foreign_or_different_artifact_cannot_be_shown_as_reviewed():
    artifact,review=basis()
    with pytest.raises(ValueError,match='differs from retained review'):
        revision_acceptance_view(**review,artifact=artifact|{'session_id':'foreign'},spent_credits=4.2)


def test_architecture_view_has_typed_components_and_foreign_source_rejects():
    from vmodel_contracts.architecture import ArchitectureOverview
    artifact=ArchitectureOverview.model_validate({'session_id':'s','stack_entries':[],
        'components':[{'name':'Review Panel','responsibility':'Show Current Result','requirement_ids':['R-1']}],
        'decisions':[],'non_functionals':{'privacy':'Owner Only'}}).model_dump(mode='json')
    _,review=basis();body=review['review']|{'phase':'architecture','regenerated_artifact_sha256':hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result=revision_acceptance_view(review=body,review_sha256=digest,artifact=artifact,spent_credits=0)
    assert result['view']['phase_title']=='Architecture'
    assert {'title':'Review Panel','description':'Show Current Result'} in result['view']['artifact_rows']
    assert 'R-1' not in json.dumps(result['view'])
    body['semantic_session_id']='foreign';digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(ValueError,match='semantic source'):
        revision_acceptance_view(review=body,review_sha256=digest,artifact=artifact,spent_credits=0)
