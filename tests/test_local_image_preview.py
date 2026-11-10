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
    env=tmp_path/'private.env';env.write_text('DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=fictional-public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456');env.chmod(0o600)
    monkeypatch.setattr(m,'inspect_image',lambda image:{'Id':image})
    monkeypatch.setattr(m,'command',lambda *a,**k:pytest.fail('mutation during review'))
    result=m.preview(state(),env_file=str(env))
    assert result['state']=='reviewed' and result['image_id']==ID


def test_missing_acceptance_cannot_preview(tmp_path):
    value=state();value.state['stages'].pop('local-acceptance')
    with pytest.raises(m.WorkflowError,match='exact current local acceptance'):m.preview(value,env_file=str(tmp_path/'none'))


def test_browser_failure_keeps_container_and_named_private_evidence(tmp_path,monkeypatch):
    import json
    import subprocess
    import builtins
    value=state();value.path=tmp_path/'state.json'
    env=tmp_path/'private.env';env.write_text('DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=fictional-public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456');env.chmod(0o600)
    monkeypatch.setattr(m,'inspect_image',lambda image:{'Id':image})
    monkeypatch.setattr(builtins,'input',lambda prompt='':'yes')
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if 'inspect' in args:return json.dumps({'Image':ID,'State':{'Running':True,'Health':{'Status':'healthy'}}})
        return ''
    monkeypatch.setattr(m,'command',command)
    def run(args,**kwargs):
        if 'inspect' in args:return subprocess.CompletedProcess(args,1,b'',b'')
        assert str(D.parents[1]/'scripts/ops/browser_smoke.py') in args
        return subprocess.CompletedProcess(args,1,'browser errors during boot: HTTP 500 /api/tasks\n','')
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(m.WorkflowError,match='browser boot unverified'):m.preview(value,env_file=str(env),apply=True)
    assert not any('rm' in args for args in calls)
    evidence=tmp_path/'browser-smoke.log'
    assert 'HTTP 500 /api/tasks' in evidence.read_text()
    assert evidence.stat().st_mode&0o777==0o600


def test_declined_preview_never_runs_docker(tmp_path,monkeypatch):
    import builtins
    env=tmp_path/'private.env';env.write_text('DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=fictional-public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456');env.chmod(0o600)
    monkeypatch.setattr(m,'inspect_image',lambda value:{'Id':value})
    monkeypatch.setattr(builtins,'input',lambda prompt='':'no')
    monkeypatch.setattr(m,'command',lambda *args,**kwargs:pytest.fail('Docker mutation after decline'))
    with pytest.raises(m.WorkflowError,match='declined'):m.preview(state(),env_file=str(env),apply=True)


QUOTED=('DELIVERY_DATABASE_URL="postgresql://fixture/db"\nDELIVERY_SUPABASE_URL="https://fixture.supabase.co"\n'
        'DELIVERY_SUPABASE_ANON_KEY="fictional-public"\nINTERVIEW_ENGINE_TOKEN="fictional-private-token-123456"\n')
UNQUOTED=('DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\n'
          'DELIVERY_SUPABASE_ANON_KEY=fictional-public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\n')


def _apply_stubs(tmp_path,monkeypatch,inspect_state,exists):
    import builtins, json, subprocess
    value=state();value.path=tmp_path/'state.json'
    env=tmp_path/'private.env';env.write_text(QUOTED);env.chmod(0o600)
    monkeypatch.setattr(m,'inspect_image',lambda image:{'Id':image})
    monkeypatch.setattr(builtins,'input',lambda prompt='':'yes')
    runs=[]
    def command(args,**kwargs):
        runs.append(args)
        if 'inspect' in args:return json.dumps({'Image':ID,'State':inspect_state})
        return ''
    monkeypatch.setattr(m,'command',command)
    payload=json.dumps([{'State':{'Running':exists}}]).encode()
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a[0],0 if exists is not None else 1,payload,b''))
    return value,env,runs


def test_apply_hands_container_exact_unquoted_values(tmp_path,monkeypatch):
    value,env,runs=_apply_stubs(tmp_path,monkeypatch,{'Running':False,'ExitCode':2},None)
    with pytest.raises(m.WorkflowError) as error:m.preview(value,env_file=str(env),apply=True)
    text=str(error.value)
    assert 'exit code 2' in text and 'docker logs' in text
    run=next(a for a in runs if a and a[0]=='docker' and 'run' in a)
    handed=Path(run[run.index('--env-file')+1])
    assert handed!=env and handed.read_text()==UNQUOTED and handed.stat().st_mode&0o077==0


def test_running_preview_is_stopped_renamed_and_replaced(tmp_path,monkeypatch):
    """2026-11-10: `req preview --apply` refused with 'docker stop requi-preview-exec'. The owner already typed yes to the
    replacement; the old container is stopped and renamed (kept for rollback), then the accepted image starts."""
    value,env,runs=_apply_stubs(tmp_path,monkeypatch,{'Running':True,'Health':{'Status':'healthy'}},True)
    monkeypatch.setattr(m,'run_browser_smoke',lambda *a:pytest.fail('browser smoke is outside this test'),raising=False)
    with pytest.raises(BaseException):m.preview(value,env_file=str(env),apply=True)
    docker=[a for a in runs if a and a[0]=='docker']
    kinds=[a[1] for a in docker]
    assert kinds.index('stop')<kinds.index('rename')<kinds.index('run')
    stop=next(a for a in docker if a[1]=='stop');rename=next(a for a in docker if a[1]=='rename')
    assert stop[2]=='requi-preview-exec' and rename[2]=='requi-preview-exec' and rename[3].startswith('requi-preview-exec-')
    assert not any(a[1] in ('rm','kill') for a in docker)


def test_exited_preview_is_renamed_not_stopped_or_removed(tmp_path,monkeypatch):
    value,env,runs=_apply_stubs(tmp_path,monkeypatch,{'Running':False,'ExitCode':2},False)
    with pytest.raises(m.WorkflowError):m.preview(value,env_file=str(env),apply=True)
    kinds=[a[1] for a in runs if a and a[0]=='docker']
    assert 'stop' not in kinds and kinds.index('rename')<kinds.index('run') and 'rm' not in kinds


def test_no_existing_container_is_started_without_stop_or_rename(tmp_path,monkeypatch):
    value,env,runs=_apply_stubs(tmp_path,monkeypatch,{'Running':False,'ExitCode':2},None)
    with pytest.raises(m.WorkflowError):m.preview(value,env_file=str(env),apply=True)
    kinds=[a[1] for a in runs if a and a[0]=='docker']
    assert 'stop' not in kinds and 'rename' not in kinds and 'run' in kinds
