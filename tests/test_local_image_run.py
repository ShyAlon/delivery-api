import importlib.util
import json
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('local_image_run',D/'local_image_run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ID='sha256:'+'a'*64


def test_cleanup_refuses_replaced_resource(tmp_path,monkeypatch):
    run=m.OwnedRun(tmp_path,ID);run.created=[('network',run.network),('container',run.service)]
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if 'inspect' in args:return json.dumps({'requi.image-gate':'foreign'})
        pytest.fail('foreign resource removal attempted')
    monkeypatch.setattr(m,'command',command);run.close()
    assert len(calls)==2 and all('inspect' in args for args in calls)


def test_cleanup_owned_resources_in_reverse_order(tmp_path,monkeypatch):
    run=m.OwnedRun(tmp_path,ID)
    run.created=[('network',run.network),('volume',run.volume),('container',run.fixture),('container',run.service)]
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        return json.dumps({'requi.image-gate':run.suffix}) if 'inspect' in args else ''
    monkeypatch.setattr(m,'command',command);run.close()
    removals=[args for args in calls if 'inspect' not in args]
    assert removals==[['docker','rm','--force',run.service],['docker','rm','--force',run.fixture],
                      ['docker','volume','rm',run.volume],['docker','network','rm',run.network]]
    assert run.created==[]


def test_partial_create_failure_does_not_claim_resource(tmp_path,monkeypatch):
    run=m.OwnedRun(tmp_path,ID)
    monkeypatch.setattr(m,'command',lambda *a,**k:(_ for _ in ()).throw(m.WorkflowError('failed')))
    with pytest.raises(m.WorkflowError):run.create('network',run.network,['docker','network','create',run.network])
    assert run.created==[]


def test_service_run_image_mismatch_refuses_http_success(tmp_path,monkeypatch):
    run=m.OwnedRun(tmp_path,ID);run.base='http://127.0.0.1:8001';run.handoff={'token':'fictional'}
    output=tmp_path/'output.json';output.write_text('{}')
    class Result:returncode=0
    calls=[]
    def invoke(args,**kwargs):
        calls.append((args,kwargs));return Result()
    monkeypatch.setattr(m.subprocess,'run',invoke)
    monkeypatch.setattr(m,'command',lambda *a,**k:'sha256:'+'b'*64)
    with pytest.raises(m.WorkflowError,match='running image changed'):run.http_acceptance(output)
    args,kwargs=calls[0]
    assert '--image-local' in args and 'fictional' not in args and kwargs['input']=='fictional'


def test_active_stop_requires_observed_inflight_call_and_single_stop(tmp_path,monkeypatch):
    import image_acceptance
    run=m.OwnedRun(tmp_path,ID);run.base='http://127.0.0.1:8001';run.handoff={'token':'fictional'}
    calls=[]
    def api(method,path,body=None):
        calls.append((method,path,body))
        if path=='/api/tasks':return {'task_id':'active-fixture'}
        return {'state':'stopped'}
    monkeypatch.setattr(image_acceptance,'API',lambda *args:api)
    monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps({'active':True}))
    proof=run.active_stop()
    assert proof['active-call-observed'] and proof['stop']=='passed'
    assert sum(path.endswith('/stop') for _,path,_ in calls)==1
    assert 'EXACT_IMAGE_STOP' in calls[0][2]['brief']


def test_external_fixture_context_readable_under_private_umask(tmp_path):
    import os
    root=D.parents[1];destination=tmp_path/'context';destination.mkdir()
    old=os.umask(0o077)
    try:m.context(root,destination)
    finally:os.umask(old)
    files=list(destination.rglob('*'))
    assert files
    for path in files:
        assert path.stat().st_mode & (0o005 if path.is_dir() else 0o004)==(0o005 if path.is_dir() else 0o004),path.relative_to(destination)


def test_exited_candidate_fails_immediately_with_exit_state(monkeypatch,tmp_path):
    run=m.OwnedRun(tmp_path,ID)
    monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps({'Status':'exited','Running':False,'ExitCode':2,'OOMKilled':False,'Error':''}))
    with pytest.raises(m.WorkflowError,match='exited.*exit=2.*oom=False'):run.check_container(run.service)


def test_failure_evidence_is_retained_before_cleanup_and_redacts_handoff(monkeypatch,tmp_path,capsys):
    run=m.OwnedRun(tmp_path,ID);run.diagnostic_path=tmp_path/'retained.log'
    run.handoff={'token':'fictional-secret-token','dsn':'postgresql:///test_tenant_x?host=/fixture/socket'}
    run.created=[('container',run.fixture),('container',run.service)]
    def command(args,**kw):
        if '.Config.Labels' in ' '.join(args):return json.dumps({'requi.image-gate':run.suffix})
        if 'inspect' in args:return json.dumps({'Status':'exited','ExitCode':2,'OOMKilled':False,'Error':'fixture error'})
        return 'Traceback: Connection refused postgresql:///test_tenant_x?host=/fixture/socket token=fictional-secret-token\n'
    monkeypatch.setattr(m,'command',command)
    run.diagnose(m.WorkflowError('startup failed'))
    result=run.diagnostic_path.read_text();terminal=capsys.readouterr().err
    assert 'Connection refused' in result and 'ExitCode' in result and 'exited' in terminal
    assert 'fictional-secret-token' not in result+terminal and 'postgresql:///' not in result+terminal
    assert run.diagnostic_path.stat().st_mode&0o077==0
    assert run.created # reporting does not silently skip cleanup


def test_unhealthy_container_refuses_without_waiting_for_global_timeout(monkeypatch,tmp_path):
    run=m.OwnedRun(tmp_path,ID)
    monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps({'Status':'running','Running':True,'ExitCode':0,'OOMKilled':False,'Health':{'Status':'unhealthy'}}))
    with pytest.raises(m.WorkflowError,match='running.*exit=0'):run.check_container(run.service)


def test_diagnostics_do_not_read_replaced_foreign_container_logs(monkeypatch,tmp_path,capsys):
    run=m.OwnedRun(tmp_path,ID);run.created=[('container',run.service)]
    run.diagnostic_path=tmp_path/'retained.log'
    def command(args,**kw):
        assert '.Config.Labels' in ' '.join(args)
        return json.dumps({'requi.image-gate':'foreign'})
    monkeypatch.setattr(m,'command',command)
    run.diagnose(m.WorkflowError('failed'))
    assert 'ownership differs' in run.diagnostic_path.read_text()
