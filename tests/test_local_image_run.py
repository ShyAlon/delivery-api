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
    seen=[]
    def command(args,**kw):
        seen.append(args)
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


def test_healthy_candidate_refused_host_is_fast_mapping_diagnostic(monkeypatch,tmp_path):
    import urllib.request,urllib.error
    run=m.OwnedRun(tmp_path,ID)
    target=m.Target(ID,run.service,'/tmp/private.env',8001,run.network,publish=False)
    class Opener:
        def open(self,*a,**kw):raise urllib.error.URLError(ConnectionRefusedError('refused'))
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    calls=[]
    monkeypatch.setattr(run,'check_container',lambda name:{'Health':{'Status':'healthy'}})
    monkeypatch.setattr(m,'command',lambda *a,**kw:calls.append(a) or '127.0.0.1:8001')
    with pytest.raises(m.WorkflowError,match='container healthy.*http://127.0.0.1:8001/health.*docker port=127.0.0.1:8001'):run.wait_host(target)
    assert len(calls)==1


def test_missing_actual_mapping_refuses_instead_of_trusting_requested_publish(monkeypatch,tmp_path):
    import urllib.request,urllib.error
    run=m.OwnedRun(tmp_path,ID);target=m.Target(ID,run.service,'/tmp/env',8001,run.network)
    class Opener:
        def open(self,*a,**kw):raise urllib.error.URLError('refused')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(run,'check_container',lambda name:{})
    monkeypatch.setattr(m,'command',lambda *a,**kw:'')
    with pytest.raises(m.WorkflowError,match='publication missing/different'):run.wait_host(target)


def test_host_deadline_is_elapsed_time_not_number_of_slow_docker_polls(monkeypatch,tmp_path):
    import urllib.request,urllib.error
    run=m.OwnedRun(tmp_path,ID);target=m.Target(ID,run.service,'/tmp/env',8001,run.network)
    clock=[0.0];calls=[]
    class Opener:
        def open(self,*a,**kw):clock[0]+=1;raise urllib.error.URLError('refused')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    def inspect(name):clock[0]+=3;calls.append(name);return {}
    monkeypatch.setattr(run,'check_container',inspect)
    with pytest.raises(m.WorkflowError,match='deadline exceeded'):run.wait_host(target,seconds=3)
    assert len(calls)==1


def test_running_service_failure_retains_bounded_private_diagnostics(monkeypatch,tmp_path,capsys):
    run=m.OwnedRun(tmp_path,ID);run.created=[('container',run.service)]
    run.diagnostic_path=tmp_path/'failure.log'
    seen=[]
    def command(args,**kw):
        seen.append(args)
        if '.Config.Labels' in ' '.join(args):return json.dumps({'requi.image-gate':run.suffix})
        if 'inspect' in args:return json.dumps({'Running':True})
        return 'child.failure.class ValueError'
    monkeypatch.setattr(m,'command',command)
    from types import SimpleNamespace
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='healthy',stderr=''))
    run.diagnose(m.WorkflowError('failed'))
    assert any('diagnostics.jsonl' in ' '.join(args) for args in seen)
    assert 'bounded service diagnostic tail' in run.diagnostic_path.read_text()
