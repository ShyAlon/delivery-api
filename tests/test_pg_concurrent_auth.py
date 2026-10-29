"""Signed PG request concurrency,not embedded-replica cursor emulation."""
import concurrent.futures
import threading
from fastapi.testclient import TestClient
from delivery_api import auth
from delivery_api.tenancy import tenant_context


def test_concurrent_signed_pg_answers_never_lose_valid_session(monkeypatch,pg_workspace_factory,pg_task_app):
    from delivery_api import cloud_runtime
    # Admit the fake engine route only;real signature/session/RLS remain intact.
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(auth,'limiter',auth.RateLimiter())
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            task=workspace.store.create_task('Concurrent signed answers')['task_id']
            workspace.store.set_engine_session(task,'concurrent-engine-session')
            workspace.store.update_phase(task,'intent','running')
        from uuid import uuid4
        from vmodel_contracts.interactive import EngineSnapshot,InputKind,InputRequest,NextStep,ProcessStatus,SubmitInputResponse,InteractiveEvent,EventType
        pending=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Answer')
        class ConcurrentEngine:
            def snapshot(self,_):return EngineSnapshot(session_id='concurrent-engine-session',task_id=task,sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=pending)
            def submit(self,_,response):
                return SubmitInputResponse(accepted_event=InteractiveEvent(event_id=uuid4(),session_id='concurrent-engine-session',task_id=task,phase_id=1,sequence=3,event_type=EventType.INPUT_ACCEPTED,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN),snapshot=EngineSnapshot(session_id='concurrent-engine-session',task_id=task,sequence=3,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN))
        monkeypatch.setattr('delivery_api.app.engine_client',ConcurrentEngine())
        application=pg_task_app(workspace)
        headers=workspace.headers
        barrier=threading.Barrier(8)
        def post(i):
            barrier.wait(timeout=15)
            with TestClient(application) as client:
                reply=client.post(f'/api/tasks/{task}/answer',json={'answer':f'answer {i}'},headers=headers)
                return reply.status_code,reply.text
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(post,range(24)))
        assert [status for status,_ in replies]==[200]*24,replies
        with tenant_context(workspace.scope):
            answers=[e for e in workspace.store.events(task) if e['kind']=='answer']
            assert len(answers)==24
