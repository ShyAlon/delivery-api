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


def test_build_reuse_inspects_immutable_id_and_does_not_rebuild(tmp_path,monkeypatch):
    image_id='sha256:'+'a'*64
    state=m.StageState(tmp_path/'state.json',{'sources':{'delivery-api':'b'*40}})
    state.complete('preflight',{});state.complete('build',{'image_id':image_id,'sources':state.inputs['sources']})
    seen=[]
    def inspect(value):
        seen.append(value);return {'Id':image_id,'Os':'linux','Architecture':'amd64'}
    monkeypatch.setattr(m,'inspect_image',inspect)
    monkeypatch.setattr(m,'source_snapshot',lambda root:pytest.fail('rebuild during valid reuse'))
    assert m.build(tmp_path,state)['image_id']==image_id
    assert seen==[image_id]


def test_invalid_reuse_and_source_drift_never_build(tmp_path,monkeypatch):
    state=m.StageState(tmp_path/'state.json',{'sources':{'delivery-api':'b'*40}})
    state.complete('preflight',{});state.complete('build',{'image_id':'bad','sources':state.inputs['sources']})
    monkeypatch.setattr(m,'inspect_image',lambda value:(_ for _ in ()).throw(m.WorkflowError('gone')))
    monkeypatch.setattr(m,'source_snapshot',lambda root:{'delivery-api':'c'*40})
    with pytest.raises(m.WorkflowError,match='source changed'):m.build(tmp_path,state)


@pytest.mark.parametrize('image',[{'Id':'latest','Os':'linux','Architecture':'amd64'},
                                  {'Id':'sha256:'+'a'*64,'Os':'linux','Architecture':'arm64'}])
def test_candidate_inspection_rejects_wrong_arch_or_mutable_id(monkeypatch,image):
    import json
    monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps(image))
    with pytest.raises(m.WorkflowError):m.inspect_image('candidate')


def test_local_acceptance_always_cleans_up_and_cannot_earn_receipt_on_failure(tmp_path,monkeypatch):
    import local_image_run
    state=m.StageState(tmp_path/'state.json',{'runtime_contract':'fixture'})
    state.complete('preflight',{});state.complete('build',{})
    calls=[]
    class Run:
        def __init__(self,*args):pass
        def start(self):calls.append('start')
        def http_acceptance(self,path):raise m.WorkflowError('acceptance failed')
        def close(self):calls.append('close')
    monkeypatch.setattr(local_image_run,'OwnedRun',Run)
    monkeypatch.setattr(m,'inspect_image',lambda value:{'Id':value})
    with pytest.raises(m.WorkflowError):m.local_acceptance(tmp_path,state,{'image_id':'fixture'})
    assert calls==['start','close']
    assert 'local-acceptance' not in state.state['stages'] and 'receipt' not in state.state['stages']
