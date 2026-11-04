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
    monkeypatch.setattr(builtins,'input',lambda prompt:'yes')
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
    monkeypatch.setattr(builtins,'input',lambda prompt:'no')
    monkeypatch.setattr(m,'command',lambda *args,**kwargs:pytest.fail('Docker mutation after decline'))
    with pytest.raises(m.WorkflowError,match='declined'):m.preview(state(),env_file=str(env),apply=True)
