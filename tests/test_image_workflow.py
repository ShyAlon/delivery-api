import importlib.util
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker'
sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('image_workflow',D/'image_workflow.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)


def test_resume_revalidates_evidence_not_a_boolean(tmp_path):
    state=m.StageState(tmp_path/'space path'/'state.json',{'source':'a'})
    state.complete('preflight',{'source':'a'})
    again=m.StageState(state.path,{'source':'a'})
    assert again.reusable('preflight',lambda evidence:evidence=={'source':'a'})
    assert not again.reusable('preflight',lambda evidence:False)
    assert not again.reusable('preflight',lambda evidence:1)
    assert not again.reusable('build',lambda evidence:True)


def test_source_drift_refuses_resume(tmp_path):
    path=tmp_path/'state.json';m.StageState(path,{'source':'a'}).complete('preflight',{})
    with pytest.raises(m.WorkflowError,match='inputs changed'):m.StageState(path,{'source':'b'})


def test_no_stage_skip_and_replay_invalidates_downstream(tmp_path):
    state=m.StageState(tmp_path/'state.json',{})
    with pytest.raises(m.WorkflowError):state.complete('receipt',{})
    state.complete('preflight',{});state.complete('build',{'id':'fictional'})
    state.complete('local-acceptance',{'checks':'fictional'})
    state.complete('build',{'id':'different'})
    assert set(state.state['stages'])=={'preflight','build'}


@pytest.mark.parametrize('raw',[b'{',b'[]',b'{"schema":1,"input_key":"x","stages":[]}'])
def test_bad_resume_refused(tmp_path,raw):
    path=tmp_path/'state.json';path.write_bytes(raw)
    with pytest.raises((m.WorkflowError,m.InvalidReceipt)):m.StageState(path,{})


def test_missing_checkout_fails_before_docker_or_network(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(m,'command',lambda *a,**k:calls.append(a))
    with pytest.raises(m.WorkflowError,match='missing source checkout'):m.preflight(tmp_path,check_tools=False)
    assert calls==[]


def test_missing_docker_fails_before_source_and_no_server(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(m.shutil,'which',lambda name:None if name=='docker' else '/bin/'+name)
    assert m.main(['--repos-root',str(tmp_path),'--run-dir',str(tmp_path/'run'),'--review'])==2
    assert 'missing local tool: docker' in capsys.readouterr().err
    assert not (tmp_path/'run').exists()
