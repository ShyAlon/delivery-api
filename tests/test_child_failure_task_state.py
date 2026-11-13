"""Actual child crash -> supervisor -> virtual lifecycle -> durable phase/API.

No provider is used. The worker seam removes admission unrelated to failure
reporting; child protocol/reaping, owner checks and PG task state remain real.
"""
import asyncio,json,sys
import pytest
from fastapi.testclient import TestClient
from delivery_api import inprocess_engine as ie
from delivery_api.child_supervisor import ChildSupervisor,ChildRunError
from delivery_api.runner import ProcessRunner
from delivery_api.tenancy import session_root
from test_child_supervisor import operation
from requi_child_contract import decode_operation
from test_child_supervisor import frame


def test_contract_failure_reaches_owned_task_without_private_exception_values(
        pg_scoped_workspace,pg_task_app,tmp_path,monkeypatch):
    ws=pg_scoped_workspace;store=ws.store
    tid=store.create_task('Child contract failure')['task_id']
    sid='failure-fixture';store.set_engine_session(tid,sid)
    base=tmp_path/'artifacts';root=session_root(base,ws.scope,sid)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(base))
    script=tmp_path/'crash.py'
    script.write_text("import os,socket,json,sys\ns=socket.socket(fileno=int(os.environ['REQUI_CHILD_FD']))\ns.makefile('rb').readline()\nsys.stderr.write('PRIVATE credential raw prompt\\n')\nsys.stderr.write('REQUI_CHILD_FAILURE '+json.dumps({'error_class':'UserFacingError','frames':[],'cause_classes':['ModelStructureError']})+'\\n')\nsys.stderr.flush()\nraise SystemExit(1)\n")
    engine=ie.ChildPhaseLifecycle(base,gateway_factory=None)
    class Worker:
        async def execute(self,*args):
            proc=ie.take(tid);proc.started.set()
            await ChildSupervisor([sys.executable,str(script)]).run(
                decode_operation(frame(operation())),authorize=lambda op:None)
    monkeypatch.setattr(engine,'_worker',lambda *args:Worker(),raising=False)
    monkeypatch.setattr(engine,'_produce',lambda *args:None,raising=False)
    class Orchestrator:
        async def execute_phase(self,*args,**kwargs):
            return await ProcessRunner(store).run(tid,['fixture','--out',str(root/'spec')],
                cwd=tmp_path,phase='architecture',env={'VMODEL_ARTIFACTS_DIR':str(root.parent)})
    async def run():
        with pytest.raises(ChildRunError) as raised:
            await engine.dispatch(store=None,orchestrator=Orchestrator(),task_id=tid,
                phase='architecture',job_id='fixture',options={})
        assert raised.value.observed_returncode==1
    asyncio.run(run())
    with TestClient(pg_task_app(ws),headers=ws.headers) as client:
        response=client.get(f'/api/tasks/{tid}')
        assert response.status_code==200
        task=response.json()
    phase=next(p for p in task['phases'] if p['phase_key']=='architecture')
    assert phase['state']=='failed'
    assert 'required structure' in phase['detail']
    assert 'cost credits' in phase['detail']
    errors=[e for e in task['events'] if e['kind']=='error']
    assert errors[-1]['payload']['exit_code']==1
    text=json.dumps(task)
    assert 'PRIVATE' not in text and 'raw prompt' not in text
    assert 'exit code -9' not in text
