from uuid import uuid4
from fastapi.testclient import TestClient
from vmodel_contracts.interactive import *
import delivery_api.app as module
from delivery_api.store import SQLiteStore

class FakeEngine:
 def __init__(self):self.pending=InputRequest(input_id=uuid4(),kind=InputKind.APPROVAL,prompt='Approve?',options=[InputOption(value=1,label='Approve'),InputOption(value=0,label='Reject')]);self.submitted=[]
 def start(self,request):
  state=EngineSnapshot(session_id='s',task_id=request.task_id,sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=self.pending)
  event=InteractiveEvent(event_id=uuid4(),session_id='s',task_id=request.task_id,phase_id=1,sequence=1,event_type=EventType.RUN_STARTED,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN)
  return StartSessionResponse(snapshot=state,accepted_event=event)
 def snapshot(self,sid):return EngineSnapshot(session_id='s',task_id='t',sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=self.pending)
 def submit(self,sid,response):self.submitted.append(response);return SubmitInputResponse(accepted_event=InteractiveEvent(event_id=uuid4(),session_id='s',task_id='t',phase_id=1,sequence=3,event_type=EventType.INPUT_ACCEPTED,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN),snapshot=EngineSnapshot(session_id='s',task_id='t',sequence=3,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN))

def test_start_and_answer_use_http_engine_not_runner(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'x.db'));task=store.create_task('Dungeon master','A virtual dungeon master');engine=FakeEngine()
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 client=TestClient(module.app);client.post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'ollama'}).raise_for_status()
 client.post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1'}).raise_for_status()
 assert engine.submitted[0].input_id==engine.pending.input_id
 assert engine.submitted[0].value==1

def test_task_snapshot_projects_http_open_input(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'snap.db'));task=store.create_task('Dungeon master','brief');engine=FakeEngine()
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine);store.set_engine_session(task['task_id'],'s')
 store.update_phase(task['task_id'],'intent','running')
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['input_state']['waiting'] is True
 assert body['input_state']['decision']['input_id']==str(engine.pending.input_id)


def test_http_engine_session_survives_delivery_restart(tmp_path,monkeypatch):
 path=tmp_path/'restart.db';first=SQLiteStore(str(path));task=first.create_task('Restart safe','brief');engine=FakeEngine()
 monkeypatch.setattr(module,'store',first);monkeypatch.setattr(module,'engine_client',engine)
 client=TestClient(module.app);client.post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'ollama'}).raise_for_status()
 assert first.engine_session(task['task_id'])=='s'
 reopened=SQLiteStore(str(path));monkeypatch.setattr(module,'store',reopened)
 body=client.get(f"/api/tasks/{task['task_id']}").json()
 assert body['input_state']['waiting'] is True
 client.post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1'}).raise_for_status()
 assert engine.submitted[-1].value==1
