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


class FailingEngine(FakeEngine):
 def submit(self,sid,response):
  from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
  from delivery_api.engine_client import EngineClientError
  raise EngineClientError(503,ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR,detail='model failed',retryable=True))

def test_engine_failure_is_not_misreported_as_dead_subprocess(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'failure.db'));task=store.create_task('Failure truth','brief');engine=FailingEngine();store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 response=TestClient(module.app).post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1','decision_id':str(engine.pending.input_id)})
 assert response.status_code==503 and response.json()['detail']['retryable'] is True
 assert 'process_live' not in response.text and 'subprocess' not in response.text


def test_smoke_choice_answer_satisfies_required_rationale():
 from delivery_api.smoke import typed_smoke_answer
 from interview_engine.conops_stage import decision_reply
 decision={'kind':2,'rationale_required':True,'options':[{'value':'1','label':'Native app','summary':'x','advantages':['a'],'disadvantages':['d']},{'value':'2','label':'PWA','summary':'y','advantages':['b'],'disadvantages':['e']}]}
 answer=typed_smoke_answer(decision)
 options=[{'concept_id':'concept:native-app','name':'Native app','summary':'x','advantages':['a'],'disadvantages':['d']},{'concept_id':'concept:pwa','name':'PWA','summary':'y','advantages':['b'],'disadvantages':['e']}]
 selected,rationale=decision_reply(answer,options)
 assert selected.name=='Native app' and rationale

def test_smoke_answers_key_off_numeric_input_kind():
 from delivery_api.smoke import typed_smoke_answer
 assert typed_smoke_answer({'kind':3,'rationale_required':False,'options':[{'value':1,'label':'Approve'}]})=='1'
 assert typed_smoke_answer({'kind':1,'options':[]}).startswith('Two adults')


def test_rationale_is_preserved_as_typed_field(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'rationale.db'));task=store.create_task('Rationale typed','brief');engine=FakeEngine()
 engine.pending=InputRequest(input_id=uuid4(),kind=InputKind.CHOICE,prompt='Choose',options=[InputOption(value='1',label='One')],rationale_required=True)
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 response=TestClient(module.app).post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1 - because it fits','decision_id':str(engine.pending.input_id)})
 assert response.status_code==200
 assert engine.submitted[-1].value=='1' and engine.submitted[-1].rationale=='because it fits'

def test_required_rationale_cannot_be_dropped(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'missing-rationale.db'));task=store.create_task('Rationale required','brief');engine=FakeEngine()
 engine.pending=InputRequest(input_id=uuid4(),kind=InputKind.CHOICE,prompt='Choose',options=[InputOption(value='1',label='One')],rationale_required=True)
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 response=TestClient(module.app).post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1','decision_id':str(engine.pending.input_id)})
 assert response.status_code==409 and not engine.submitted
