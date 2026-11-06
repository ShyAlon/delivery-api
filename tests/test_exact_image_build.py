"""Real git-archive build orchestration, fake Docker only. No image execution claim."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('exact_image_build_workflow',D/'image_workflow.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ID='sha256:'+'a'*64


def git(path,*args):
    env=dict(os.environ,GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.test',GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.test')
    return subprocess.run(['git',*args],cwd=path,env=env,capture_output=True,text=True,check=True).stdout.strip()


@pytest.fixture
def build_inputs(tmp_path,monkeypatch):
    root=tmp_path/'source space';root.mkdir()
    for name in m.REPOS:
        path=root/name;path.mkdir();git(path,'init','-q','-b','main')
        (path/'source.py').write_text('VALUE=1\n')
        if name=='delivery-api':(path/'Dockerfile.full').write_text('FROM scratch\n')
        git(path,'add','.');git(path,'commit','-qm','fixture')
        remote=tmp_path/('remote-'+name);git(tmp_path,'init','-q','--bare',str(remote))
        git(path,'remote','add','origin',str(remote));git(path,'push','-q','origin','main')
    sources=m.source_snapshot(root)
    state=m.StageState(tmp_path/'private run'/'state.json',{'sources':sources})
    state.complete('preflight',{})
    original=m.command;calls=[];contexts=[]
    def command(args,**kwargs):
        calls.append(args)
        if args[0]=='git':return original(args,**kwargs)
        assert args[0]=='docker','unexpected external surface'
        if 'build' in args:
            context=Path(args[-1])
            contexts.append({str(p.relative_to(context)):(p.read_bytes(),p.stat().st_mode&0o777) for p in context.rglob('*') if p.is_file()})
            return ''
        if 'inspect' in args:return json.dumps({'Id':ID,'Os':'linux','Architecture':'amd64'})
        pytest.fail('unexpected Docker action '+str(args))
    monkeypatch.setattr(m,'command',command)
    return root,state,calls,contexts


def test_build_is_local_amd64_pins_all_full_commits_and_carries_timestamp(build_inputs):
    root,state,calls,contexts=build_inputs
    evidence=m.build(root,state)
    build=next(args for args in calls if args[:2]==['docker','build'])
    assert build[build.index('--platform')+1]=='linux/amd64'
    for name,sha in state.inputs['sources'].items():assert name+'='+sha in ' '.join(build)
    assert any(arg.startswith('REQUI_BUILD_ID=sha256:') for arg in build)
    assert any(arg.startswith('REQUI_BUILD_AT=') for arg in build)
    assert evidence['image_id']==ID and evidence['sources']==state.inputs['sources']
    assert not any(args[0]=='ssh' or 'push' in args or 'run' in args for args in calls)
    assert 'local-acceptance' not in state.state['stages']


def test_archived_source_readable_even_with_private_umask(build_inputs):
    root,state,calls,contexts=build_inputs
    prior=os.umask(0o077)
    try:m.build(root,state)
    finally:os.umask(prior)
    assert contexts and len(contexts[0])==5
    assert all(mode&0o044==0o044 for _,mode in contexts[0].values())
    assert all(data==b'VALUE=1\n' for name,(data,_) in contexts[0].items() if name.endswith('source.py'))


def test_dirty_checkout_refuses_before_docker_build(build_inputs):
    root,state,calls,contexts=build_inputs
    (root/'interview-engine/source.py').write_text('VALUE=2\n')
    with pytest.raises(m.WorkflowError,match='tracked source changes'):m.build(root,state)
    assert not contexts and not any('build' in args for args in calls)


def test_untracked_secret_never_enters_context(build_inputs):
    root,state,calls,contexts=build_inputs
    (root/'delivery-api/.env').write_text('FICTIONAL_SECRET=must-not-export')
    m.build(root,state)
    assert all(not name.endswith('.env') for name in contexts[0])
    assert not any(b'must-not-export' in data for data,_ in contexts[0].values())


def test_changed_commit_refuses_after_snapshot_before_build(build_inputs):
    root,state,calls,contexts=build_inputs
    path=root/'spec-generator';(path/'source.py').write_text('VALUE=2\n');git(path,'add','.');git(path,'commit','-qm','changed')
    with pytest.raises(m.WorkflowError,match='source changed'):m.build(root,state)
    assert not contexts


def test_failed_build_produces_no_acceptance_or_receipt(build_inputs,monkeypatch):
    root,state,calls,contexts=build_inputs;original=m.command
    def command(args,**kwargs):
        if args[:2]==['docker','build']:raise m.WorkflowError('fixed build failure')
        return original(args,**kwargs)
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(m.WorkflowError,match='build failure'):m.build(root,state)
    assert set(state.state['stages'])=={'preflight'}
    assert not (state.path.parent/'accepted-image.json').exists()


def test_wrong_architecture_after_build_never_accepted(build_inputs,monkeypatch):
    root,state,calls,contexts=build_inputs
    monkeypatch.setattr(m,'inspect_image',lambda value:(_ for _ in ()).throw(m.WorkflowError('candidate is not linux/amd64')))
    with pytest.raises(m.WorkflowError,match='linux/amd64'):m.build(root,state)
    assert 'build' not in state.state['stages']


def test_archive_symlink_refused_before_build(build_inputs):
    root,state,calls,contexts=build_inputs
    path=root/'verification-loop';(path/'outside').symlink_to('/etc/passwd');git(path,'add','.');git(path,'commit','-qm','symlink')
    state.inputs['sources']=m.source_snapshot(root)
    with pytest.raises(m.WorkflowError,match='contains links'):m.build(root,state)
    assert not contexts


def test_silent_command_failure_names_exit_and_oom_without_echoing_secrets(monkeypatch):
    class Result:returncode=137;stdout='';stderr=''
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:Result())
    with pytest.raises(m.WorkflowError,match='exit code 137; no state advanced; produced no output, possible signal kill or out of memory'):
        m.command(['docker','build'])
    Result.stderr='FICTIONAL_SECRET=must-not-print'
    with pytest.raises(m.WorkflowError) as error:m.command(['docker','build'])
    assert 'must-not-print' not in str(error.value)


def test_build_records_plain_cache_log_and_context_size(build_inputs,capsys):
    root,state,calls,contexts=build_inputs
    m.build(root,state)
    args=next(args for args in calls if args[:2]==['docker','build'])
    assert args[args.index('--progress')+1]=='plain'
    assert 'committed files /' in capsys.readouterr().err


def test_dependency_install_layer_does_not_copy_product_wheels_first():
    text=(D.parents[1]/'Dockerfile.full').read_text()
    requirements=text.index('COPY --from=build /wheels/requirements.txt')
    install=text.index('pip install -r /wheels/requirements.txt')
    wheels=text.index('COPY --from=build /wheels/ /wheels/')
    assert requirements<install<wheels
    assert '--mount=type=cache,target=/root/.cache/pip' in text


def test_dockerfile_no_run_continuation_consumes_next_copy():
    text=(D.parents[1]/'Dockerfile.full').read_text()
    assert "'pyarrow>=17,<24'\\\n\nCOPY" not in text
