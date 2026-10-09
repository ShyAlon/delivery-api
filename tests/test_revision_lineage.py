from copy import deepcopy
from types import SimpleNamespace
import hashlib,json
import pytest
from delivery_api.revision_lineage import plan_revision_lineage
from test_revision_intent import proposal
from test_closed_revision_job import options


def setup():
    cap=SimpleNamespace(scope=SimpleNamespace(owner_id='owner'),task_id='task',session_id='s',generation=1)
    artifact={'session_id':'semantic','intent_model_ref':'semantic'}
    p=proposal()|{'artifact':artifact,'artifact_sha256':hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    return dict(capability=cap,semantic_session_id='semantic',new_execution_session_id='execution2',
        options=options(),retained_proposal={'proposal':p,'document_sha256':'d'*64})


def test_lineage_is_stable_without_modifying_source_or_installing():
    args=setup();before=deepcopy(args)
    result=plan_revision_lineage(**args);body=result['lineage']
    assert body['semantic_session_id']=='semantic' and body['parent_execution_session_id']=='s'
    assert body['execution_session_id']=='execution2' and body['generation']==2
    assert not body['installed'] and not body['dispatch_started']
    assert args==before and result==plan_revision_lineage(**args)


@pytest.mark.parametrize('destination',['s','semantic','../escape',''])
def test_rejects_reused_or_invalid_execution_namespace(destination):
    args=setup();args['new_execution_session_id']=destination
    with pytest.raises(ValueError):plan_revision_lineage(**args)


@pytest.mark.parametrize('change',[{'session_id':'foreign'},{'task_id':'foreign'},{'generation':2}])
def test_rejects_foreign_parent(change):
    args=setup();args['retained_proposal']['proposal'].update(change)
    with pytest.raises(ValueError):plan_revision_lineage(**args)


def test_repeat_revision_keeps_original_semantic_id():
    args=setup();args['capability'].session_id='execution2';args['capability'].generation=2
    args['options']['artifact_evidence'][0]['generation']=2
    args['retained_proposal']['proposal'].update(session_id='execution2',generation=2)
    args['new_execution_session_id']='execution3'
    body=plan_revision_lineage(**args)['lineage']
    assert body['semantic_session_id']=='semantic' and body['parent_execution_session_id']=='execution2' and body['generation']==3


def test_wrong_semantic_identity_cannot_be_inferred_from_execution():
    args=setup();args['semantic_session_id']='s'
    with pytest.raises(ValueError,match='semantic identity'):plan_revision_lineage(**args)
