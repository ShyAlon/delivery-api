import importlib.util
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('remote_image_apply',D/'remote_image_apply.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from runtime_spec import Target
m.Target=Target
from image_settings import validate
m.validate_settings=validate
ID='sha256:'+'a'*64


@pytest.fixture(autouse=True)
def native_host_contract(monkeypatch):
    monkeypatch.setattr(m,'host_preflight',lambda:None)


def plan(tmp_path):
    env=tmp_path/'private.env';env.write_text('DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=public\nINTERVIEW_ENGINE_TOKEN=fixture-private-token-123456789\n');env.chmod(0o600)
    args=Target(ID,'requi-service',str(env),8000,'service').arguments()
    return dict(reference='ghcr.io/shyalon/require@'+ID,config_digest=ID,name='requi-service',
                env_file=str(env),port=8000,network='service',username='shyalon',token='fictional-token',runtime_arguments=args,previous_container_id=None,previous_image_id=None)


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


def test_transmitted_program_compiles_as_one_script():
    combined=(D/'runtime_spec.py').read_text()+'\n'+(D/'image_settings.py').read_text()+'\nvalidate_settings=validate\n'+(D/'remote_image_apply.py').read_text()
    compile(combined,'remote-exact-image','exec')


def test_candidate_run_failure_restores_prior_service(tmp_path,monkeypatch):
    import json
    value=plan(tmp_path);calls=[]
    image={'Id':ID,'Os':'linux','Architecture':'amd64','RepoDigests':[value['reference']]}
    current={'Image':'sha256:'+'b'*64,'Config':{'Labels':{'requi.shared-runtime':'1'}},'State':{'Running':True}}
    candidate={'Image':ID,'Config':{'Labels':{'requi.shared-runtime':'1'}},'State':{'Running':False}}
    class Result:
        returncode=0
        stdout=json.dumps(current)
    def run(args,**kwargs):
        result=Result()
        if 'inspect' in args and any('rollback' in x for x in args):result.stdout=json.dumps(current)
        elif 'inspect' in args and any('run' in x for x in calls[-1] if isinstance(x,str)):result.stdout=json.dumps(candidate)
        elif 'inspect' in args and any('run' in call for call in calls):result.stdout=json.dumps(candidate)
        return result
    def call(args,**kwargs):
        calls.append(args)
        if 'image' in args and 'inspect' in args:return json.dumps(image)
        if 'run' in args:raise RuntimeError('run failed after container create')
        if 'inspect' in args:return json.dumps(current)
        return ''
    # Explicit deterministic inspect responses: initial current, then partial candidate.
    responses=iter([current,candidate])
    def subprocess_run(args,**kwargs):
        result=Result();result.stdout=json.dumps(next(responses));return result
    monkeypatch.setattr(m,'call',call);monkeypatch.setattr(m.subprocess,'run',subprocess_run)
    with pytest.raises(RuntimeError,match='previous restart'):m.apply(value)
    assert any(args[:4]==['sudo','-n','docker','stop'] for args in calls)
    assert ['sudo','-n','docker','rm','--force','requi-service'] in calls
    assert ['sudo','-n','docker','start','requi-service'] in calls


def test_old_unlabeled_container_not_adopted_by_name_alone(tmp_path,monkeypatch):
    import json
    value=plan(tmp_path);calls=[]
    image={'Id':ID,'Os':'linux','Architecture':'amd64','RepoDigests':[value['reference']]}
    current={'Id':'b'*64,'Name':'/requi-service','Config':{'Labels':{}},'HostConfig':{'PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8000'}]}}}
    class Result:returncode=0;stdout=json.dumps(current)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:Result())
    def call(args,**kwargs):
        calls.append(args);return json.dumps(image) if 'inspect' in args else ''
    monkeypatch.setattr(m,'call',call)
    with pytest.raises(ValueError,match='exact reviewed previous'):m.apply(value)
    assert not any('stop' in args for args in calls)


def test_remote_missing_required_settings_refuses_before_registry_pull(tmp_path,monkeypatch):
    value=plan(tmp_path);Path(value['env_file']).write_text('FICTIONAL=only\n')
    monkeypatch.setattr(m,'call',lambda *a,**k:pytest.fail('remote command before settings validation'))
    with pytest.raises(ValueError,match='PostgreSQL settings required'):m.apply(value)


def test_remote_arm_host_refused_before_login_or_stop(tmp_path,monkeypatch):
    value=plan(tmp_path);calls=[]
    def call(args,**kw):
        calls.append(args)
        if args==['uname','-m']:return 'aarch64'
        pytest.fail('remote registry/service command before architecture check')
    monkeypatch.setattr(m,'call',call)
    # Exercise the actual host guard even though other command tests stub it.
    import importlib.util
    spec=importlib.util.spec_from_file_location('remote_guard',D/'remote_image_apply.py')
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    monkeypatch.setattr(guard,'call',call)
    with pytest.raises(ValueError,match='linux/amd64 host required'):guard.host_preflight()
    assert calls==[['uname','-m']]
