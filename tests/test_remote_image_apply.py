import importlib.util
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('remote_image_apply',D/'remote_image_apply.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from runtime_spec import Target
m.Target=Target
ID='sha256:'+'a'*64


def plan(tmp_path):
    env=tmp_path/'private.env';env.write_text('FICTIONAL=value\n');env.chmod(0o600)
    args=Target(ID,'requi-service',str(env),8000,'service').arguments()
    return dict(reference='ghcr.io/shyalon/require@'+ID,config_digest=ID,name='requi-service',
                env_file=str(env),port=8000,network='service',username='shyalon',token='fictional-token',runtime_arguments=args)


def test_runtime_argument_tamper_rejected_before_any_command(tmp_path,monkeypatch):
    value=plan(tmp_path);value['runtime_arguments'].insert(-1,'--privileged')
    monkeypatch.setattr(m,'call',lambda *a,**k:pytest.fail('command before runtime validation'))
    with pytest.raises(ValueError,match='shared specification'):m.apply(value)


def test_world_readable_settings_refused_before_any_command(tmp_path,monkeypatch):
    value=plan(tmp_path);Path(value['env_file']).chmod(0o644)
    monkeypatch.setattr(m,'call',lambda *a,**k:pytest.fail('command before settings validation'))
    with pytest.raises(ValueError,match='private existing'):m.apply(value)


def test_pull_mismatch_never_stops_existing_container(tmp_path,monkeypatch):
    import json
    value=plan(tmp_path);calls=[]
    def call(args,**kwargs):
        calls.append((args,kwargs))
        if 'inspect' in args:return json.dumps({'Id':'wrong','Os':'linux','Architecture':'amd64'})
        return ''
    monkeypatch.setattr(m,'call',call)
    with pytest.raises(ValueError,match='pulled image differs'):m.apply(value)
    assert not any('stop' in args for args,_ in calls)
    login=next((args,kw) for args,kw in calls if 'login' in args)
    assert login[0][0]=='docker' and login[1]['secret']=='fictional-token'
    assert all('fictional-token' not in args for args,_ in calls)
