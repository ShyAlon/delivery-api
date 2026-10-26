"""Triage markers on the start-completion path (2026-10-25 stall).

The live stall showed the last start-path line at model.route.resolved with
the question never surfacing. These tests pin the two decisive markers:
engine.start.response (the exact snapshot shape the engine returned - the
line that separates an engine-side null from a delivery-side hang) and
engine.start.persisted (every start write committed, with duration)."""
import asyncio
import logging
from uuid import uuid4

import delivery_api.app as module
from delivery_api.store import create_store
from vmodel_contracts.interactive import (EngineSnapshot,InteractiveEvent,NextStep,
    ProcessStatus,StartSessionResponse)


def _response(session_id,task_id):
    snapshot=EngineSnapshot(session_id=session_id,task_id=task_id,sequence=1,
        process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN,open_input=None)
    event=InteractiveEvent(event_id=uuid4(),session_id=session_id,task_id=task_id,phase_id=0,sequence=1,
        event_type=10,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN)
    return StartSessionResponse(snapshot=snapshot,accepted_event=event)


def _run_success(tmp_path,monkeypatch):
    store=create_store(str(tmp_path/'test.db'))
    store.create_task('t1',task_id='task1')
    session_id='00000000-0000-0000-0000-0000000000aa'
    store.begin_interview_start('task1',session_id,'route')
    class FakeEngine:
        def start(self,request):return _response(session_id,'task1')
    class NoWatch:
        @staticmethod
        async def advance(*args,**kwargs):return None
    monkeypatch.setattr(module,'store',store)
    monkeypatch.setattr(module,'engine_client',FakeEngine())
    monkeypatch.setattr(module,'_watch_engine_advance',NoWatch.advance)
    asyncio.run(module._complete_interview_start('task1',session_id,'ollama',None,'idea',
        {'probing':['ollama','phi4:latest']},'owner'))
    return store


def test_response_marker_pins_the_exact_engine_shape(tmp_path,monkeypatch,caplog):
    with caplog.at_level(logging.INFO,logger='delivery_api.activity'):
        _run_success(tmp_path,monkeypatch)
    lines=[r.getMessage() for r in caplog.records if 'engine.start.response' in r.getMessage()]
    assert len(lines)==1,caplog.records
    line=lines[0]
    assert "sequence='1'" in line and "status='20'" in line and "open_input='null'" in line,line


def test_persisted_marker_follows_all_start_writes(tmp_path,monkeypatch,caplog):
    with caplog.at_level(logging.INFO,logger='delivery_api.activity'):
        store=_run_success(tmp_path,monkeypatch)
    msgs=[r.getMessage() for r in caplog.records]
    assert any('engine.start.persisted' in m and 'dur_ms=' in m for m in msgs),msgs
    kinds=[e['kind'] for e in store.events('task1')]
    assert 'model_route' in kinds and 'engine_protocol' in kinds,kinds
