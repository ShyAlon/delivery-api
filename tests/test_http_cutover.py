from uuid import uuid4
from fastapi.testclient import TestClient
from vmodel_contracts.interactive import *
import delivery_api.app as module
from delivery_api.store import SQLiteStore

class FakeEngine:
 def preview_route(self,route):
  model='deepseek-flash' if route=='deepseek' else 'qwen2.5-coder:14b'
  return {'route':route,'model_map':{stage:[route,model] for stage in ('probing','distillation','phrasing','scope_tiebreak','expert_opinion','conops')}}
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


def test_smoke_checkpoint_resumes_after_mid_interview_interruption(tmp_path):
 from delivery_api.smoke import load_smoke_checkpoint,save_smoke_checkpoint
 path=tmp_path/'checkpoint.json';expected={'task_id':'task-1','model_route':'ollama','answers':6,'last_input_id':'input-6','status':'active'}
 save_smoke_checkpoint(path,expected)
 assert load_smoke_checkpoint(path)==expected
 save_smoke_checkpoint(path,{**expected,'answers':7,'last_input_id':'input-7'})
 assert load_smoke_checkpoint(path)['answers']==7


class TextEngine(FakeEngine):
 def __init__(self,prompt='What is the goal and core value?'):
  super().__init__()
  self.pending=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt=prompt)

def test_text_question_yields_answer_composer_mode_not_an_empty_decision(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'text.db'));task=store.create_task('Text question','brief');engine=TextEngine()
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['ui_state']['mode']=='engine_question'
 assert body['ui_state']['decision'] is None
 assert body['ui_state']['question']=='What is the goal and core value?'
 assert body['ui_state']['input_id']==str(engine.pending.input_id)

def test_choice_input_still_projects_a_structured_decision(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'choice.db'));task=store.create_task('Choice question','brief');engine=FakeEngine()
 engine.pending=InputRequest(input_id=uuid4(),kind=InputKind.CHOICE,prompt='Pick one',options=[InputOption(value='1',label='One'),InputOption(value='2',label='Two')])
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['ui_state']['mode']=='engine_decision'
 assert body['ui_state']['decision']['input_id']==str(engine.pending.input_id)
 assert len(body['ui_state']['decision']['options'])==2

def test_waiting_input_event_synced_once_per_engine_input(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'sync.db'));task=store.create_task('Sync','brief');engine=TextEngine()
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 client=TestClient(module.app)
 first=client.get(f"/api/tasks/{task['task_id']}").json()
 waiting=[e for e in first['events'] if e['kind']=='waiting_input']
 assert len(waiting)==1 and waiting[0]['payload']['question']=='What is the goal and core value?'
 assert waiting[0]['payload']['input_id']==str(engine.pending.input_id)
 second=client.get(f"/api/tasks/{task['task_id']}").json()
 assert len([e for e in second['events'] if e['kind']=='waiting_input'])==1
 engine.pending=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Second question')
 third=client.get(f"/api/tasks/{task['task_id']}").json()
 synced=[e for e in third['events'] if e['kind']=='waiting_input']
 assert len(synced)==2 and synced[-1]['payload']['question']=='Second question'
 assert third['ui_state']['question']=='Second question'
 assert third['ui_state']['input_id']!=first['ui_state']['input_id']

def test_engine_waiting_without_open_input_stays_executing(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'noinput.db'));task=store.create_task('No input','brief');engine=TextEngine()
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 engine.snapshot=lambda sid:EngineSnapshot(session_id='s',task_id='t',sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=None)
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['ui_state']['mode']=='executing'
 assert body['input_state']['waiting'] is False


def test_slow_engine_start_does_not_block_other_requests(tmp_path,monkeypatch):
 import asyncio,threading
 import httpx
 store=SQLiteStore(str(tmp_path/'concurrent-start.db'));task=store.create_task('Slow engine','brief')
 entered=threading.Event();release=threading.Event();engine=FakeEngine()
 def slow_start(request):
  entered.set()
  assert release.wait(5), 'test engine start was never released'
  return FakeEngine.start(engine,request)
 engine.start=slow_start
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 async def exercise():
  transport=httpx.ASGITransport(app=module.app)
  async with httpx.AsyncClient(transport=transport,base_url='http://test') as client:
   pending=asyncio.create_task(client.post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'ollama'}))
   try:
    await asyncio.wait_for(asyncio.to_thread(entered.wait,2),timeout=3)
    assert entered.is_set()
    health=await asyncio.wait_for(client.get('/health'),timeout=1)
    assert health.status_code==200
   finally:release.set()
   assert (await asyncio.wait_for(pending,timeout=3)).status_code==200
 asyncio.run(exercise())


def test_slow_engine_submit_does_not_block_other_requests(tmp_path,monkeypatch):
 import asyncio,threading
 import httpx
 store=SQLiteStore(str(tmp_path/'concurrent-answer.db'));task=store.create_task('Slow answer','brief')
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 entered=threading.Event();release=threading.Event();engine=FakeEngine()
 def slow_submit(sid,response):
  entered.set()
  assert release.wait(5), 'test engine submit was never released'
  return FakeEngine.submit(engine,sid,response)
 engine.submit=slow_submit
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 async def exercise():
  transport=httpx.ASGITransport(app=module.app)
  async with httpx.AsyncClient(transport=transport,base_url='http://test') as client:
   pending=asyncio.create_task(client.post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1'}))
   try:
    await asyncio.wait_for(asyncio.to_thread(entered.wait,2),timeout=3)
    assert entered.is_set()
    health=await asyncio.wait_for(client.get('/health'),timeout=1)
    assert health.status_code==200
   finally:release.set()
   assert (await asyncio.wait_for(pending,timeout=3)).status_code==200
 asyncio.run(exercise())


def test_start_records_interview_flash_not_downstream_chat(tmp_path, monkeypatch):
 store=SQLiteStore(str(tmp_path/'flash-provenance.db'))
 task=store.create_task('Flash provenance', 'An idea')
 engine=FakeEngine()
 requests=[]
 start=engine.start
 def record_start(request):
  requests.append(request)
  return start(request)
 engine.start=record_start
 monkeypatch.setattr(module,'store',store)
 monkeypatch.setattr(module,'engine_client',engine)
 TestClient(module.app).post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'deepseek'}).raise_for_status()
 assert requests[0].model_route=='deepseek'
 route=[e for e in store.events(task['task_id']) if e['kind']=='model_route' and e['payload'].get('scope')=='start'][0]
 assert route['payload']['resolved']=='deepseek:deepseek-flash'
 assert route['payload']['model']=='deepseek-flash'


def test_start_records_env_resolved_phi4_not_catalog_qwen(tmp_path, monkeypatch):
 store=SQLiteStore(str(tmp_path/'phi4-provenance.db'))
 task=store.create_task('Phi4 provenance', 'An idea')
 class Phi4Engine(FakeEngine):
  def preview_route(self,route):
   assert route=='ollama'
   return {'route':route,'model_map':{stage:['ollama','phi4'] for stage in ('probing','distillation','phrasing','scope_tiebreak','expert_opinion','conops')}}
 engine=Phi4Engine()
 monkeypatch.setattr(module,'store',store)
 monkeypatch.setattr(module,'engine_client',engine)
 TestClient(module.app).post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'ollama'}).raise_for_status()
 route=[e for e in store.events(task['task_id']) if e['kind']=='model_route' and e['payload'].get('scope')=='start'][0]
 assert route['payload']['resolved']=='ollama:phi4'
 assert route['payload']['model']=='phi4'


def test_route_preview_shows_conflict_without_switching(monkeypatch):
 from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
 class ConflictEngine(FakeEngine):
  def preview_route(self,route):
   if route=='deepseek':
    from delivery_api.engine_client import EngineClientError
    raise EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail="selected route 'deepseek' conflicts with INTERVIEW_ENGINE_MODEL_PROBING='ollama:phi4'"))
   return {'route':'ollama','model_map':{stage:['ollama','phi4'] for stage in ('probing','distillation','phrasing','scope_tiebreak','expert_opinion','conops')}}
 monkeypatch.setattr(module,'engine_client',ConflictEngine())
 routes=module.model_routes(a=None)['routes']
 assert routes[0]['available'] and routes[0]['model']=='ollama:phi4'
 assert routes[1]['available'] is False
 assert 'INTERVIEW_ENGINE_MODEL_PROBING' in routes[1]['error']
