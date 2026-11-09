"""Warm supervisor: parked child serves follow-ups on the same process."""
import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest
from delivery_api.child_supervisor import ChildSupervisor,ChildRunError
from delivery_api.child_execution_contract import decode_operation
from test_child_execution_contract import operation,frame

FIXTURE=Path(__file__).parent/'fixtures'/'child_warm_fixture.py'


def supervisor(mode):
    return ChildSupervisor([sys.executable,str(FIXTURE),mode],grace_seconds=.5,kill_seconds=.5)


def followup(operation_id):
    value=operation();value['operation_id']=operation_id
    return decode_operation(frame(value)),{'version':1,'binding':value['binding'],
        'operation':value,'input':{'text':'yes'},'input_ref':'answer-1','fds':{'broker':0}}


def test_parked_child_serves_followup_with_gifted_descriptor():
    async def go():
        s=supervisor('warm')
        op1=decode_operation(frame(operation()))
        handle=await s.spawn(op1,authorize=lambda op:None)
        events=[]
        await s.run_operation(handle,op1,on_event=events.append,first=True,park=True)
        assert [e.state for e in events]==['ready','running','completed']
        pid=handle.process.pid
        gift,peer=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        op2,payload=followup('op-2')
        await s.run_operation(handle,op2,on_event=events.append,payload=payload,fds=[gift.fileno()],park=True)
        gift.close()
        assert peer.recv(1)==b'k';peer.close()
        assert handle.process.pid==pid  # same process, no respawn
        assert [e.state for e in events]==['ready','running','completed','running','completed']
        assert events[3].operation_id=='op-2' and events[3].sequence==4
        result=await s.stop_parked(handle,op2,persist_stop=lambda op:None)
        assert result.returncode==0 and result.reaped and not s.active
    asyncio.run(go())


def test_close_parked_recycles_without_stop():
    async def go():
        s=supervisor('warm-eof')
        op1=decode_operation(frame(operation()))
        handle=await s.spawn(op1,authorize=lambda op:None)
        await s.run_operation(handle,op1,first=True,park=True)
        op2,payload=followup('op-2')
        gift,peer=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        await s.run_operation(handle,op2,payload=payload,fds=[gift.fileno()],park=True)
        gift.close();peer.close()
        result=await s.close_parked(handle,op2)
        assert result.returncode==0 and result.reaped and not s.active
    asyncio.run(go())


def test_forged_trailing_frame_fails_the_next_operation():
    async def go():
        s=supervisor('warm-trailing')
        op1=decode_operation(frame(operation()))
        handle=await s.spawn(op1,authorize=lambda op:None)
        await s.run_operation(handle,op1,first=True,park=True)
        op2,payload=followup('op-2')
        gift,peer=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        with pytest.raises(ChildRunError,match='identity or sequence changed'):
            await s.run_operation(handle,op2,payload=payload,fds=[gift.fileno()],park=True)
        gift.close();peer.close()
        await s.reap(handle,op2,failed=True,failure_error=ChildRunError('test teardown'))
        assert not s.active
    asyncio.run(go())


def test_first_rejects_payload_and_followup_requires_one():
    async def go():
        s=supervisor('warm')
        op1=decode_operation(frame(operation()))
        handle=await s.spawn(op1,authorize=lambda op:None)
        with pytest.raises(ValueError):
            await s.run_operation(handle,op1,first=True,payload={'x':1},park=True)
        with pytest.raises(ValueError):
            await s.run_operation(handle,op1,park=True)
        await s.reap(handle,op1,failed=True,failure_error=ChildRunError('test teardown'))
    asyncio.run(go())


def test_parked_slot_stays_occupied_until_reap():
    async def go():
        s=supervisor('warm')
        op1=decode_operation(frame(operation()))
        handle=await s.spawn(op1,authorize=lambda op:None)
        await s.run_operation(handle,op1,first=True,park=True)
        with pytest.raises(ChildRunError,match='slot'):
            await s.spawn(decode_operation(frame(operation())),authorize=lambda op:None)
        await s.reap(handle,op1,failed=True,failure_error=ChildRunError('test teardown'))
    asyncio.run(go())
