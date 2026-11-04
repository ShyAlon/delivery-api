import importlib.util
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('local_image_preview',D/'local_image_preview.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ID='sha256:'+'a'*64


def state():
    class State:pass
    value=State();value.inputs={'runtime_contract':'contract'}
    value.state={'stages':{'build':{'image_id':ID},'local-acceptance':{'image_id':ID,'runtime_contract':'contract',
                 'checks':{name:'passed' for name in ('startup','signed-http','child-lifecycle','stop','retained-outputs')}}}}
    return value


def test_dry_preview_has_no_container_mutation(tmp_path,monkeypatch):
    env=tmp_path/'private.env';env.write_text('FICTIONAL=value');env.chmod(0o600)
    monkeypatch.setattr(m,'inspect_image',lambda image:{'Id':image})
    monkeypatch.setattr(m,'command',lambda *a,**k:pytest.fail('mutation during review'))
    result=m.preview(state(),env_file=str(env))
    assert result['state']=='reviewed' and result['image_id']==ID


def test_missing_acceptance_cannot_preview(tmp_path):
    value=state();value.state['stages'].pop('local-acceptance')
    with pytest.raises(m.WorkflowError,match='exact current local acceptance'):m.preview(value,env_file=str(tmp_path/'none'))
