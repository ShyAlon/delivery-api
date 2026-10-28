"""Real local processes only. No app config, network, credentials or user DB."""
import asyncio
import os
from pathlib import Path
import signal
import sys

import pytest
from delivery_api.child_supervisor import ChildSupervisor, ChildRunError
from delivery_api.child_execution_contract import decode_operation
from test_child_execution_contract import operation,frame

FIXTURE=Path(__file__).parent/'fixtures'/'child_worker_fixture.py'


def run(coro):return asyncio.run(coro)


def supervisor(mode='complete'):
    return ChildSupervisor([sys.executable,str(FIXTURE),mode],grace_seconds=.1,kill_seconds=.1)


def test_default_is_not_selected_by_runtime():
    from delivery_api import cloud_runtime
    assert 'child_supervisor' not in Path(cloud_runtime.__file__).read_text()


def test_real_child_lifecycle_and_secret_free_environment(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','do-not-inherit')
    monkeypatch.setenv('DELIVERY_DATABASE_URL','do-not-inherit')
    events=[];s=supervisor()
    result=run(s.run(decode_operation(frame(operation())),authorize=lambda op:None,on_event=events.append))
    assert result.returncode==0 and result.reaped and result.provider_reconciled is False
    assert [e.state for e in events]==['ready','running','completed']
    assert not s.active


def test_invalid_authority_never_spawns(monkeypatch):
    s=supervisor();calls=[]
    async def spawn(*a,**kw):calls.append(a);raise AssertionError('spawn forbidden')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    def deny(op):raise PermissionError('revoked')
    with pytest.raises(PermissionError,match='revoked'):run(s.run(decode_operation(frame(operation())),authorize=deny))
    assert calls==[] and not s.active


@pytest.mark.parametrize('mode',['bad-frame','foreign','crash','duplicate','stuck'])
def test_invalid_or_stuck_child_is_reaped_and_slot_freed(mode):
    s=supervisor(mode)
    with pytest.raises(ChildRunError):run(s.run(decode_operation(frame(operation())),authorize=lambda op:None,timeout_seconds=.3))
    assert not s.active and s.last_result.reaped
    with pytest.raises(ProcessLookupError):os.kill(s.last_result.pid,0)


def test_stop_persists_fence_before_signal_and_never_claims_provider_quiescence():
    async def go():
        s=supervisor('cooperative');events=[];stopped=[]
        stop=asyncio.Event()
        def event(e):
            events.append(e)
            if e.state=='running':stop.set()
        result=await s.run(decode_operation(frame(operation())),authorize=lambda op:None,
            on_event=event,stop=stop,persist_stop=lambda op:stopped.append(op.operation_id))
        assert stopped==['op-1'] and result.reaped and not result.provider_reconciled
        assert events[-1].state=='stopped'
    run(go())


def test_parent_task_cancellation_reaps_child():
    async def go():
        s=supervisor('stuck');ready=asyncio.Event()
        task=asyncio.create_task(s.run(decode_operation(frame(operation())),authorize=lambda op:None,on_event=lambda e:ready.set()))
        await asyncio.wait_for(ready.wait(),3);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert s.last_result.reaped and not s.active
    run(go())


def test_single_slot_is_held_until_stuck_child_is_reaped():
    async def go():
        s=supervisor('stuck');ready=asyncio.Event()
        one=asyncio.create_task(s.run(decode_operation(frame(operation())),authorize=lambda op:None,on_event=lambda e:ready.set(),timeout_seconds=.3))
        await ready.wait()
        with pytest.raises(ChildRunError,match='slot'):await s.run(decode_operation(frame(operation())),authorize=lambda op:None)
        with pytest.raises(ChildRunError):await one
        assert not s.active and s.last_result.reaped
    run(go())


def test_stop_fence_failure_is_not_reported_as_success():
    async def go():
        s=supervisor('cooperative');stop=asyncio.Event()
        def fence(op):raise PermissionError('stop persist failed')
        with pytest.raises(PermissionError,match='persist failed'):
            await s.run(decode_operation(frame(operation())),authorize=lambda op:None,
                stop=stop,persist_stop=fence,on_event=lambda e:stop.set())
        assert s.last_result.reaped
    run(go())


def test_unbounded_child_frame_is_failure_not_a_memory_sink():
    s=supervisor('oversize')
    with pytest.raises(ChildRunError):run(s.run(decode_operation(frame(operation())),authorize=lambda op:None,timeout_seconds=.3))
    assert s.last_result.reaped


def test_stubborn_grandchild_group_is_killed_after_leader_exits(tmp_path):
    async def go():
        # Fixed local fixture records its process-group descendant for inspection.
        target=tmp_path/'grandchild.pid'
        s=ChildSupervisor([sys.executable,str(FIXTURE),'descendant',str(target)],grace_seconds=.1,kill_seconds=.1)
        result=await s.run(decode_operation(frame(operation())),authorize=lambda op:None)
        assert result.reaped
        pid=int(target.read_text())
        for _ in range(100):
            stat=Path(f'/proc/{pid}/stat')
            if not stat.exists() or stat.read_text().split()[2]=='Z':break
            await asyncio.sleep(.01)
        else:pytest.fail('descendant survived process-group cleanup')
    run(go())


def test_private_config_and_separate_descriptors_are_not_environment_secrets(tmp_path,monkeypatch):
    import socket
    fixture=tmp_path/'config-child.py'
    fixture.write_text('''import os,socket,json
f=socket.socket(fileno=int(os.environ['REQUI_CHILD_FD'])).makefile('rwb',buffering=0)
op=json.loads(f.readline())
c=socket.socket(fileno=int(os.environ['REQUI_CHILD_CONFIG_FD'])).makefile('rb')
assert json.loads(c.readline())=={'dsn':'private-not-env'}
b=socket.socket(fileno=int(os.environ['REQUI_CHILD_BROKER_FD']))
b.sendall(b'broker-separate')
assert all('private-not-env' not in v for v in os.environ.values())
for i,state in enumerate(('ready','running','completed'),1):
 f.write(json.dumps({'version':1,'operation_id':op['operation_id'],'sequence':i,'state':state}).encode()+b'\\n')
''')
    a,b=socket.socketpair()
    try:
        s=ChildSupervisor([sys.executable,str(fixture)])
        result=run(s.run(decode_operation(frame(operation())),authorize=lambda op:None,
            private_config={'dsn':'private-not-env'},descriptors={'broker':b.fileno()}))
        assert result.reaped and a.recv(100)==b'broker-separate'
    finally:a.close();b.close()


def test_terminal_child_failure_has_safe_parent_evidence(tmp_path):
 import logging
 from delivery_api import diagnostic_evidence as d
 path=tmp_path/'evidence';h=d.EvidenceHandler(path);log=logging.getLogger('delivery_api.diagnostic');log.addHandler(h)
 op=decode_operation(frame(operation()));s=supervisor('crash')
 try:
  with pytest.raises(ChildRunError):run(s.run(op,authorize=lambda op:None,timeout_seconds=.3))
 finally:log.removeHandler(h);h.close()
 text=d.read(path,task_id=op.binding.task_id)['text']
 assert 'child.lifecycle.failed' in text and op.binding.job_id in text
