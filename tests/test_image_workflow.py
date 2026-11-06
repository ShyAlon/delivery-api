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


def test_receipt_requires_matching_accepted_config(tmp_path):
    state=m.StageState(tmp_path/'state.json',{})
    with pytest.raises(m.WorkflowError,match='differs'):m.receipt(state,{'config_digest':'foreign'})
    assert not (tmp_path/'accepted-image.json').exists()


def test_acceptance_hash_changes_with_script_or_schema(tmp_path):
    root=D.parents[1]
    first=m.acceptance_contract(root)
    assert first.startswith('sha256:') and len(first)==71
    assert m.acceptance_contract(root)==first
    with pytest.raises(m.WorkflowError):m.acceptance_contract(tmp_path)


def test_apply_alone_never_implies_deploy(tmp_path,capsys):
    assert m.main(['--run-dir',str(tmp_path),'--apply'])==2
    assert '--apply requires --deploy' in capsys.readouterr().err
    assert not (tmp_path/'state.json').exists()


def test_no_goal_or_conflicting_goals_fail_before_tools(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'preflight',lambda *a,**k:pytest.fail('preflight for ambiguous goal'))
    assert m.main(['--run-dir',str(tmp_path)])==2
    assert m.main(['--run-dir',str(tmp_path),'--review','--build'])==2


def test_log_uses_level_and_elapsed_without_control_sequences_off_tty(capsys):
    m.log('FAIL','fixed diagnostic')
    text=capsys.readouterr().err
    assert '[FAIL] elapsed=' in text and 's fixed diagnostic' in text
    assert '\x1b' not in text


def test_failed_preflight_prints_recovery_without_private_command_output(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(m.shutil,'which',lambda name:None)
    assert m.main(['--review','--run-dir',str(tmp_path)])==2
    text=capsys.readouterr().err
    assert 'No automatic retry or alternate build path' in text and 'run directory' in text


def test_declined_local_goal_never_builds_or_starts(tmp_path,monkeypatch,capsys):
    import builtins
    inputs={'sources':{},'runtime_contract':'fictional','acceptance_contract':'fictional'}
    monkeypatch.setattr(m,'preflight',lambda *args:inputs)
    monkeypatch.setattr(builtins,'input',lambda prompt='':'no')
    monkeypatch.setattr(m,'build',lambda *args:pytest.fail('build after decline'))
    assert m.main(['--run-dir',str(tmp_path),'--build'])==2
    assert 'declined; nothing built or started' in capsys.readouterr().err
    assert set(m.StageState(tmp_path/'state.json',inputs).state['stages'])=={'preflight'}


def test_failed_reacceptance_revokes_previous_preview_and_publication_evidence(tmp_path,monkeypatch):
    import local_image_run
    state=m.StageState(tmp_path/'state.json',{'runtime_contract':'fixture'})
    state.complete('preflight',{});state.complete('build',{'image_id':'fixture'})
    state.complete('local-acceptance',{'image_id':'fixture','checks':{'startup':'passed'}})
    state.complete('publish',{'tag':'old'});state.complete('verify-manifest',{'manifest':'old'})
    state.complete('receipt',{'manifest':'old'})
    (tmp_path/'accepted-image.json').write_text('old receipt')
    class Run:
        def __init__(self,*args):pass
        def start(self):raise m.WorkflowError('new attempt failed')
        def close(self):pass
    monkeypatch.setattr(local_image_run,'OwnedRun',Run)
    monkeypatch.setattr(m,'inspect_image',lambda value:{'Id':value})
    with pytest.raises(m.WorkflowError):m.local_acceptance(tmp_path,state,{'image_id':'fixture'})
    restored=m.StageState(state.path,state.inputs)
    assert set(restored.state['stages'])=={'preflight','build'}
    assert not (tmp_path/'accepted-image.json').exists()


def test_failure_diagnostics_precede_destructive_cleanup(tmp_path,monkeypatch):
    import local_image_run
    state=m.StageState(tmp_path/'state.json',{'runtime_contract':'fixture'})
    state.complete('preflight',{});state.complete('build',{})
    calls=[]
    class Run:
        def __init__(self,*args):pass
        def start(self):raise m.WorkflowError('container exited')
        def diagnose(self,error):
            assert self.diagnostic_path==tmp_path/'local-failure.log'
            calls.append(('diagnose',type(error).__name__,str(error)))
        def close(self):calls.append(('cleanup',))
    monkeypatch.setattr(local_image_run,'OwnedRun',Run)
    monkeypatch.setattr(m,'inspect_image',lambda value:{'Id':value})
    with pytest.raises(m.WorkflowError,match='container exited'):m.local_acceptance(tmp_path,state,{'image_id':'fixture'})
    assert calls==[('diagnose','WorkflowError','container exited'),('cleanup',)]


def test_docker_logs_stderr_is_preserved_only_for_explicit_diagnostics(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='out',stderr='Traceback on stderr'))
    assert m.command(['docker','logs'])=='out'
    assert m.command(['docker','logs'],include_stderr=True)=='outTraceback on stderr'


def _fresh_repos(tmp_path):
    for name in m.REPOS:(tmp_path/name/'.git').mkdir(parents=True)


def _snapshot_stubs(monkeypatch,head,remote,ancestor_rc):
    import subprocess
    def command(args,**kwargs):
        if args[1]=='status':return ''
        if args[1:3]==['rev-parse','HEAD']:return head
        if args[1:3]==['rev-parse','origin/main']:return remote
        if args[1]=='fetch':return ''
        raise AssertionError(args)
    monkeypatch.setattr(m,'command',command)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a[0],ancestor_rc,b'',b''))


def test_stale_sibling_refused_naming_repo_shas_and_pull(tmp_path,monkeypatch):
    _fresh_repos(tmp_path);_snapshot_stubs(monkeypatch,'a'*40,'b'*40,1)
    with pytest.raises(m.WorkflowError) as error:m.source_snapshot(tmp_path)
    text=str(error.value)
    assert 'delivery-api' in text and 'aaaaaaaa' in text and 'bbbbbbbb' in text and 'pull --ff-only' in text


def test_current_sibling_records_head_without_refusal(tmp_path,monkeypatch):
    _fresh_repos(tmp_path);_snapshot_stubs(monkeypatch,'c'*40,'b'*40,0)
    assert m.source_snapshot(tmp_path)=={name:'c'*40 for name in m.REPOS}
