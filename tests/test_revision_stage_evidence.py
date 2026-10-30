import hashlib,json
import pytest

def test_approved_upstream_reader_retains_exact_bytes_and_rejects_checkpoint_drift(tmp_path):
    from delivery_api.revision_stage_evidence import read_approved_upstream_bytes
    (tmp_path/'interview').mkdir();path=tmp_path/'interview'/'intent_model.json'
    path.write_text('{  "approved": true }\n');raw=path.read_bytes()
    expected={'interview/intent_model.json':{'approved':True}}
    assert read_approved_upstream_bytes(tmp_path,expected_documents=expected)=={'interview/intent_model.json':hashlib.sha256(raw).hexdigest()}
    assert path.read_bytes()==raw
    with pytest.raises(ValueError,match='checkpoint channel'):
        read_approved_upstream_bytes(tmp_path,expected_documents={'interview/intent_model.json':{}})


@pytest.mark.parametrize('kind',['absent','null','foreign','old','unconfirmed'])
def test_cloud_revision_requires_typed_conops_without_healing(kind):
    from pathlib import Path
    from copy import deepcopy
    from delivery_api.revision_stage_evidence import validate_cloud_revision_conops
    state=json.loads((Path(__file__).parent/'fixtures'/'revision-source.json').read_text())
    if kind=='absent':state.pop('conops')
    elif kind=='null':state['conops']=None
    elif kind=='foreign':state['conops']['session_id']='foreign'
    elif kind=='old':state['conops']['contract_version']='0.14.3'
    else:state['conops']['decision']['founder_confirmed']=False
    after=deepcopy(state)
    with pytest.raises(ValueError):validate_cloud_revision_conops(state,'s')
    assert state==after


def test_cloud_revision_conops_validator_preserves_source():
    from pathlib import Path
    from copy import deepcopy
    from delivery_api.revision_stage_evidence import validate_cloud_revision_conops
    state=json.loads((Path(__file__).parent/'fixtures'/'revision-source.json').read_text());before=deepcopy(state)
    assert validate_cloud_revision_conops(state,'s').session_id=='s' and state==before
