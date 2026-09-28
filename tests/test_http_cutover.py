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


def test_task_snapshot_distinguishes_engine_protocol_422_from_transport(tmp_path,monkeypatch):
 from delivery_api.engine_client import EngineClientError,EngineUnreachableError
 from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
 import httpx
 store=SQLiteStore(str(tmp_path/'snapshot-error.db'))
 task=store.create_task('Snapshot errors','A brief')
 store.set_engine_session(task['task_id'],'s-protocol')
 store.update_phase(task['task_id'],'intent','running')
 class Rejected(FakeEngine):
  def snapshot(self,sid):
   raise EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail='stage model contract failed',retryable=False))
 monkeypatch.setattr(module,'store',store)
 monkeypatch.setattr(module,'engine_client',Rejected())
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['ui_state']['diagnostic']['http_status']==422
 assert body['ui_state']['diagnostic']['detail']=='stage model contract failed'
 assert body['ui_state']['diagnostic']['reason']=='engine protocol rejected the snapshot'
 class Down(Rejected):
  def snapshot(self,sid):raise EngineUnreachableError('http://engine',httpx.ConnectError('refused'))
 monkeypatch.setattr(module,'engine_client',Down())
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['ui_state']['diagnostic']['reason']=='engine transport unavailable'
 assert 'http_status' not in body['ui_state']['diagnostic']

class SwitchEngine(FakeEngine):
 def __init__(self):super().__init__();self.switches=[]
 def snapshot(self,sid):return EngineSnapshot(session_id='s',task_id=self.task_id,sequence=2+len(self.switches),process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=self.pending,model_route='deepseek' if self.switches else 'ollama')
 def switch_route(self,sid,request):
  self.switches.append(request)
  payload=RouteChangePayload(actor_id=request.actor_id,from_route='ollama',to_route='deepseek',
   from_model_map={'probing':('ollama','phi4')},to_model_map={'probing':('deepseek','deepseek-flash')},
   route_version=1,session_sequence=3,idempotency_key=request.idempotency_key)
  event=InteractiveEvent(event_id=uuid4(),session_id='s',task_id=self.task_id,phase_id=1,sequence=3,
   event_type=EventType.ROUTE_CHANGED,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,route_change=payload)
  return ModelRouteSwitchResponse(accepted_event=event,snapshot=self.snapshot(sid))

def test_switch_via_live_task_endpoint_records_one_event(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'switch.db'));task=store.create_task('Switch task','brief');engine=SwitchEngine();engine.task_id=task['task_id']
 store.set_engine_session(task['task_id'],'s');store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 client=TestClient(module.app);snapshot=client.get(f"/api/tasks/{task['task_id']}/engine-snapshot").json()
 assert snapshot['open_input']['input_id']==str(engine.pending.input_id)
 body={'model_route':'deepseek','model':'deepseek-flash','expected_sequence':snapshot['sequence'],'expected_input_id':snapshot['open_input']['input_id'],'idempotency_key':str(uuid4())}
 first=client.post(f"/api/tasks/{task['task_id']}/model-route",json=body)
 assert first.status_code==200,first.text
 again=client.post(f"/api/tasks/{task['task_id']}/model-route",json=body)
 assert again.status_code==200,again.text
 route_events=[e for e in store.events(task['task_id']) if e['kind']=='model_route']
 assert len(route_events)==1 and route_events[0]['payload']['route_version']==1
 assert store.task(task['task_id'])['model_route']=='deepseek'

def test_cost_segments_preserve_consecutive_route_versions():
 rows=[{'route_version':0,'provider':'ollama','model':'phi4','prompt_tokens':10,'completion_tokens':5,'price_per_mtok_prompt_usd':0,'price_per_mtok_completion_usd':0},
       {'route_version':1,'provider':'deepseek','model':'deepseek-flash','prompt_tokens':100,'completion_tokens':20,'price_per_mtok_prompt_usd':.30,'price_per_mtok_completion_usd':1.20},
       {'route_version':2,'provider':'ollama','model':'phi4','prompt_tokens':4,'completion_tokens':2,'price_per_mtok_prompt_usd':0,'price_per_mtok_completion_usd':0}]
 segments=module._model_usage_segments(rows)
 assert [s['route_version'] for s in segments]==[0,1,2]
 assert abs(segments[1]['cost_usd']-(100*.30+20*1.20)/1_000_000)<1e-12

def test_retired_ollama_default_still_lists_installed_switch_targets(monkeypatch):
 from delivery_api.engine_client import EngineClientError
 class Engine:
  def default_route(self):return {'route':'ollama','model':'qwen2.5-coder:14b'}
  def preview_route(self,route,initial_model=None):
   if route=='ollama':raise EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail='qwen not installed'))
   return {'model_map':{'probing':['deepseek','deepseek-flash']}}
  def local_models(self):return ['phi4']
 monkeypatch.setattr(module,'engine_client',Engine())
 routes=module.model_routes(a=None)['routes']
 assert routes[0]['id']=='ollama' and routes[0]['available'] and routes[0]['models']==['phi4']

def test_recovery_does_not_reexecute_completed_http_interview(tmp_path,monkeypatch):
 import asyncio
 store=SQLiteStore(str(tmp_path/'completed.db'));task=store.create_task('Already complete','brief');tid=task['task_id']
 store.set_engine_session(tid,'complete-session');store.update_phase(tid,'intent','running')
 monkeypatch.setattr(module,'store',store)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE,open_input=None)
 monkeypatch.setattr(module,'engine_client',Complete())
 calls=[]
 async def unexpected(*args,**kwargs):calls.append(args)
 monkeypatch.setattr(module,'_resume_one_interview',unexpected)
 asyncio.run(module._recover_tasks())
 assert not calls
 assert store.engine_session(tid)=='complete-session'
 assert store.task(tid)['state']=='running'


def _completed_files(root, session):
 from vmodel_contracts import IntentModel, ConOpsDocument
 interview=root/session/'interview';interview.mkdir(parents=True)
 intent=IntentModel(session_id=session,problem_statement='p',target_users=['u'],core_value_proposition='v',in_scope=['a'],out_of_scope=['b'],
   stakeholder_needs=[{'need_id':'SN-1','statement':'s','kind':'functional','source_turns':[0]}],
   assumption_items=[{'statement':'a','source_turns':[0]}],scope_exclusions=[{'item':'x','source_turns':[1],'explicit':True}],source_turns={'problem_statement':[0]})
 conops=ConOpsDocument(session_id=session,stakeholder_context=[{'stakeholder':'u','role':'r','goals':['g'],'operating_environment':'e','source_turns':[1]}],
   operational_scenarios=[{'scenario_id':'OS-1','name':'n','trigger':'t','actors':['u'],'operational_flow':['x'],'success_outcome':'y','source_turns':[1]}],
   concept_options=[{'name':'PWA','summary':'s','advantages':['a'],'disadvantages':['d']},{'name':'Native app','summary':'s','advantages':['a'],'disadvantages':['d']}],
   decision={'selected_concept':'PWA','rationale':'r','founder_confirmed':True,'source_turns':[1]})
 (interview/'intent_model.json').write_text(intent.model_dump_json())
 (interview/'conops.json').write_text(conops.model_dump_json())
 return interview


def test_restored_terminal_session_reconciles_phase_handoff_once(tmp_path,monkeypatch):
 import asyncio
 session='restored-session';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 path=tmp_path/'restored.db';first=SQLiteStore(str(path));tid=first.create_task('Terminal restore','brief')['task_id']
 first.set_engine_session(tid,session);first.update_phase(tid,'intent','running',detail='Waiting for founder input')
 first.add_event(tid,'error','Old model failure',phase_key='intent')
 restored=SQLiteStore(str(path));monkeypatch.setattr(module,'store',restored)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 launched=[]
 async def continue_after(task_id,phase):
  launched.append((task_id,phase));restored.update_phase(task_id,'needs','running',detail='Building stakeholder needs')
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 async def recover():
  await module._recover_tasks();await asyncio.sleep(.01)
 asyncio.run(recover())
 task=restored.task(tid);states={p['phase_key']:p['state'] for p in task['phases']}
 assert states['intent']=='approved' and states['conops']=='approved' and states['needs']=='running'
 assert task['active_phase']=='needs' and task['state']=='running'
 assert launched==[(tid,'conops')]
 progress=[e for e in restored.events(tid) if e['kind']=='progress']
 assert len(progress)==1 and progress[0]['payload']['session_id']==session
 assert any(e['kind']=='error' and e['message']=='Old model failure' for e in restored.events(tid))
 assert module.get_task(tid)['active_phase']=='needs'
 asyncio.run(module._recover_tasks())
 # Once Needs has become active, interview recovery must not relaunch it.
 assert launched==[(tid,'conops')]
 assert len([e for e in restored.events(tid) if e['kind']=='progress'])==1


def test_terminal_reconcile_refuses_missing_or_mismatched_artifacts(tmp_path,monkeypatch):
 import asyncio
 session='other-session';artifacts=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 store=SQLiteStore(str(tmp_path/'missing.db'));tid=store.create_task('Incomplete','brief')['task_id']
 store.set_engine_session(tid,session);store.update_phase(tid,'intent','running')
 monkeypatch.setattr(module,'store',store)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 launched=[]
 async def continue_after(*args):launched.append(args)
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 asyncio.run(module._recover_tasks());assert store.task(tid)['phases'][0]['state']=='running' and not launched
 interview=_completed_files(artifacts,session)
 body=(interview/'conops.json').read_text().replace(session,'wrong-session');(interview/'conops.json').write_text(body)
 asyncio.run(module._recover_tasks());assert store.task(tid)['phases'][0]['state']=='running' and not launched


def test_task_read_recovers_completed_interview_and_returns_new_phase(tmp_path,monkeypatch):
 session='read-recovery';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 store=SQLiteStore(str(tmp_path/'read.db'));tid=store.create_task('Read repair','brief')['task_id']
 store.set_engine_session(tid,session);store.update_phase(tid,'intent','running',detail='Waiting for founder input')
 monkeypatch.setattr(module,'store',store)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 async def continue_after(task_id,phase):store.update_phase(task_id,'needs','running',detail='Building stakeholder needs')
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 response=TestClient(module.app).get(f'/api/tasks/{tid}')
 assert response.status_code==200,response.text
 task=response.json();states={p['phase_key']:p['state'] for p in task['phases']}
 assert states['intent']==states['conops']=='approved' and task['active_phase']=='conops'
 assert store.task(tid)['active_phase']=='needs'
 assert len([e for e in store.events(tid) if e['kind']=='progress'])==1


def test_terminal_restore_launches_real_orchestrator_from_bound_session(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.orchestrator import PhaseOrchestrator
 session='bound-session';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 generator=tmp_path/'spec-generator';(generator/'.venv/bin').mkdir(parents=True);(generator/'.venv/bin/python').write_text('')
 monkeypatch.setenv('SPEC_GENERATOR_DIR',str(generator))
 store=SQLiteStore(str(tmp_path/'handoff.db'));tid=store.create_task('Bound recovery','brief')['task_id']
 store.set_engine_session(tid,session);store.update_phase(tid,'intent','running',detail='Waiting for founder input')
 monkeypatch.setattr(module,'store',store)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 class Runner:
  active_work={};calls=[]
  def work_state(self,task_id):return None
  async def run(self,task_id,cmd,**kwargs):
   self.calls.append((cmd,kwargs));store.update_phase(task_id,'needs','running',detail='Building stakeholder needs');return 0
 runner=Runner();monkeypatch.setattr(module,'runner',runner)
 monkeypatch.setattr(module,'orchestrator',PhaseOrchestrator(store,runner))
 async def recover():
  await module._recover_tasks();await asyncio.sleep(.01)
 asyncio.run(recover())
 assert len(runner.calls)==1
 command,options=runner.calls[0]
 assert str(artifacts/session/'interview'/'intent_model.json') in command
 assert str(artifacts/session/'interview'/'conops.json') in command
 assert options['phase']=='needs' and store.task(tid)['active_phase']=='needs'
 assert command[0]==str(generator/'.venv/bin/python')


def test_final_typed_answer_reconciles_without_resubmission(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.auth import Actor
 session='final-answer';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 store=SQLiteStore(str(tmp_path/'answer.db'));tid=store.create_task('Final answer','brief')['task_id']
 store.set_engine_session(tid,session);store.update_phase(tid,'intent','running',detail='Waiting for founder input')
 monkeypatch.setattr(module,'store',store)
 pending=InputRequest(input_id=uuid4(),kind=InputKind.APPROVAL,prompt='Approve?',options=[InputOption(value=1,label='Approve')])
 class Final:
  calls=0
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=29,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=pending)
  def submit(self,sid,response):
   self.calls+=1
   event=InteractiveEvent(event_id=uuid4(),session_id=session,task_id=tid,phase_id=5,sequence=30,event_type=EventType.INPUT_ACCEPTED,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
   return SubmitInputResponse(accepted_event=event,snapshot=EngineSnapshot(session_id=session,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE))
 engine=Final();monkeypatch.setattr(module,'engine_client',engine)
 launched=[]
 async def continue_after(task_id,phase):launched.append(phase)
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 async def go():
  assert await module.answer_interview(tid,module.InterviewAnswer(answer='1',decision_id=str(pending.input_id)),Actor('test','Founder'))=={'status':'accepted'}
  await asyncio.sleep(.01)
 asyncio.run(go())
 assert engine.calls==1 and launched==['conops']
 assert {p['phase_key']:p['state'] for p in store.task(tid)['phases']}['conops']=='approved'
 assert len([e for e in store.events(tid) if e['kind']=='answer'])==1


def test_terminal_recovery_logs_exact_phase_disposition(tmp_path,monkeypatch):
 import asyncio
 session='diagnostic-session';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 store=SQLiteStore(str(tmp_path/'diagnostic.db'));tid=store.create_task('Terminal','brief')['task_id']
 store.set_engine_session(tid,session);store.update_phase(tid,'intent','running')
 monkeypatch.setattr(module,'store',store)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 notes=[];monkeypatch.setattr(module,'activity',lambda kind,message,**fields:notes.append((kind,fields)))
 async def continue_after(task_id,phase):store.update_phase(task_id,'needs','running')
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 async def recover():
  await module._recover_tasks();await asyncio.sleep(.01)
 asyncio.run(recover())
 assert next(fields for kind,fields in notes if kind=='engine.recovery.reconciled')['next_phase']=='needs'
 assert next(fields for kind,fields in notes if kind=='engine.recovery.handoff')['active_phase']=='needs'
 assert not any(kind=='engine.recovery.completed' for kind,_ in notes)
 asyncio.run(recover())
 assert next(fields for kind,fields in reversed(notes) if kind=='engine.recovery.completed')['reason']=='downstream_already_active'


def test_unpinned_legacy_snapshot_is_classified_once_without_handoff(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.engine_client import EngineClientError
 store=SQLiteStore(str(tmp_path/'legacy.db'));tid=store.create_task('Old route','brief')['task_id']
 store.set_engine_session(tid,'old-session');store.update_phase(tid,'intent','running')
 monkeypatch.setattr(module,'store',store)
 class Old:
  def snapshot(self,sid):raise EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail='legacy session has no pinned model route. It predates per-session routing.'))
 monkeypatch.setattr(module,'engine_client',Old())
 notes=[];monkeypatch.setattr(module,'activity',lambda kind,message,**fields:notes.append((kind,fields)))
 launched=[]
 async def continue_after(*args):launched.append(args)
 monkeypatch.setattr(module.orchestrator,'continue_after',continue_after)
 asyncio.run(module._recover_tasks())
 assert [kind for kind,_ in notes if kind.startswith('engine.recovery.')]==['engine.recovery.legacy_unpinned']
 assert not launched and store.task(tid)['phases'][0]['state']=='running'


def _failed_needs_task(tmp_path,monkeypatch):
 session='completed-for-needs';artifacts=tmp_path/'artifacts';_completed_files(artifacts,session)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
 store=SQLiteStore(str(tmp_path/'failed-needs.db'));tid=store.create_task('Retry needs','brief')['task_id']
 store.set_engine_session(tid,session)
 store.update_phase(tid,'intent','approved');store.update_phase(tid,'conops','approved')
 store.update_phase(tid,'needs','failed',detail='Prior engine error')
 store.add_event(tid,'error','Prior engine error',phase_key='needs',payload={'exit_code':1})
 monkeypatch.setattr(module,'store',store)
 return store,tid,session


def test_failed_needs_retry_preserves_interview_and_starts_only_needs(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 calls=[]
 async def run(task_id,phase,*,mode,model_route):calls.append((task_id,phase,mode.value,model_route));store.update_phase(task_id,'needs','running',detail='Building needs')
 monkeypatch.setattr(module.orchestrator,'execute_phase',run)
 async def exercise():
  preview=await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(),Actor('test','Founder'))
  assert preview['status']=='preview' and preview['session_id']==session and not calls
  assert store.task(tid)['state']=='failed'
  started=await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True,model_route='ollama',model='phi4'),Actor('test','Founder'))
  assert started['status']=='started'
  await asyncio.sleep(.01)
 asyncio.run(exercise())
 task=store.task(tid);states={p['phase_key']:p['state'] for p in task['phases']}
 assert states['intent']==states['conops']=='approved' and states['needs']=='running'
 assert task['state']=='running' and task['active_phase']=='needs' and store.engine_session(tid)==session
 assert calls==[(tid,'needs','start','ollama:phi4')]
 assert any(e['message']=='Prior engine error' for e in store.events(tid))
 assert len([e for e in store.events(tid) if e['kind']=='retry'])==1


def test_failed_needs_retry_is_not_automatic_and_duplicate_confirm_is_rejected(tmp_path,monkeypatch):
 import asyncio
 from fastapi import HTTPException
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 class Complete:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=30,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(module,'engine_client',Complete())
 called=[]
 async def execute(task_id,phase,*,mode,model_route):called.append(phase)
 monkeypatch.setattr(module.orchestrator,'execute_phase',execute)
 asyncio.run(module._recover_tasks())
 assert not called and store.task(tid)['state']=='failed'
 async def exercise():
  await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True,model_route='ollama',model='phi4'),Actor('test','Founder'))
  await asyncio.sleep(.01)
  try:await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True,model_route='ollama',model='phi4'),Actor('test','Founder'))
  except HTTPException as exc:assert exc.status_code==409
  else:raise AssertionError('duplicate retry accepted')
 asyncio.run(exercise())
 assert called==['needs']


def test_retry_needs_keeps_generation_and_rejects_missing_artifact(tmp_path,monkeypatch):
 import asyncio
 from fastapi import HTTPException
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 start=store.task(tid)['generation']
 (tmp_path/'artifacts'/session/'interview'/'conops.json').unlink()
 try:asyncio.run(module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True),Actor('test','Founder')))
 except HTTPException as exc:assert exc.status_code==409
 else:raise AssertionError('missing conops accepted')
 assert store.task(tid)['generation']==start and store.task(tid)['state']=='failed'


def test_rejected_confirmed_needs_retry_is_audited_and_keeps_old_failure(tmp_path,monkeypatch):
 import asyncio
 from fastapi import HTTPException
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 (tmp_path/'artifacts'/session/'interview'/'conops.json').unlink()
 try:asyncio.run(module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True),Actor('test','Founder')))
 except HTTPException as exc:
  assert exc.status_code==409 and 'artifacts' in exc.detail['cause']
 else:raise AssertionError('missing artifact accepted')
 assert store.task(tid)['state']=='failed'
 assert any(e['kind']=='error' and e['message']=='Prior engine error' for e in store.events(tid))
 assert any(e['kind']=='retry_rejected' and 'artifacts' in e['payload']['reason'] for e in store.events(tid))
 assert any(e['action']=='phase.retry_rejected' for e in store.audit_entries(tid))


def test_audit_rolls_up_http_interview_usage_without_double_counting(tmp_path,monkeypatch):
 import json
 from fastapi.testclient import TestClient
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 usage=tmp_path/'artifacts'/session/'interview'/'usage.jsonl'
 usage.write_text('\n'.join(json.dumps(x) for x in [
  {'stage':'probing','provider':'deepseek','model':'deepseek-chat','prompt_tokens':110,'completion_tokens':20,'price_per_mtok_prompt_usd':1,'price_per_mtok_completion_usd':2},
  {'stage':'distillation','provider':'deepseek','model':'deepseek-chat','prompt_tokens':60,'completion_tokens':10,'price_per_mtok_prompt_usd':1,'price_per_mtok_completion_usd':2},
  {'stage':'conops','provider':'ollama','model':'phi4','prompt_tokens':30,'completion_tokens':10,'price_per_mtok_prompt_usd':None,'price_per_mtok_completion_usd':None}])+'\n')
 store.add_event(tid,'price','Old interview subprocess price',phase_key='intent',payload={'tokens':999,'price':999})
 store.add_event(tid,'price','Downstream price',phase_key='needs',payload={'tokens':50,'price':100})
 from fastapi import Request
 request=Request({'type':'http','headers':[]})
 result=module.task_audit(tid,request)
 totals=result['totals']
 assert totals['tokens']==290 and totals['price']==100
 assert totals['interview_calls']==3 and totals['interview_unpriced_calls']==1
 assert round(totals['interview_cost_usd'],6)==0.00023


def test_retry_uses_generator_env_default_not_interview_pin_and_fresh_start(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 store.set_model_route(tid,'ollama:qwen2.5-coder:14b')
 store.add_event(tid,'model_route','Old Needs route',phase_key='needs',payload={'route':'ollama','model':'qwen2.5-coder:14b'})
 shared=tmp_path/'.env';shared.write_text('SPEC_GENERATOR_MODEL_NEEDS=ollama:phi4\n')
 generator=tmp_path/'spec-generator';generator.mkdir();monkeypatch.setenv('SPEC_GENERATOR_DIR',str(generator))
 monkeypatch.delenv('SPEC_GENERATOR_MODEL_NEEDS',raising=False)
 calls=[]
 async def execute(task_id,phase,*,mode,model_route):calls.append((phase,mode.value,model_route))
 monkeypatch.setattr(module.orchestrator,'execute_phase',execute)
 async def exercise():
  preview=await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(),Actor('test','Founder'))
  assert preview['default_model']=='ollama:phi4' and preview['mode']=='start'
  assert preview['last_failure']=='Prior engine error'
  await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True,model_route='ollama',model='phi4'),Actor('test','Founder'))
  await asyncio.sleep(.01)
 asyncio.run(exercise())
 assert calls==[('needs','start','ollama:phi4')]
 assert any(e['kind']=='model_route' and e['payload'].get('resolved')=='ollama:phi4' for e in store.events(tid))


def test_retry_with_generator_checkpoint_resumes_without_from(tmp_path,monkeypatch):
 import asyncio
 from delivery_api.auth import Actor
 store,tid,session=_failed_needs_task(tmp_path,monkeypatch)
 spec=tmp_path/'artifacts'/session/'spec';spec.mkdir();(spec/'checkpoints.sqlite').write_bytes(b'checkpoint-placeholder')
 calls=[]
 async def execute(task_id,phase,*,mode,model_route):calls.append((mode.value,model_route))
 monkeypatch.setattr(module.orchestrator,'execute_phase',execute)
 async def exercise():
  preview=await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(),Actor('test','Founder'))
  assert preview['checkpoint'] and preview['mode']=='resume'
  await module.retry_failed_needs(tid,module.FailedNeedsRetryRequest(confirm=True,model_route='ollama',model='phi4'),Actor('test','Founder'))
  await asyncio.sleep(.01)
 asyncio.run(exercise())
 assert calls==[('resume','ollama:phi4')]

def test_architecture_baseline_decision_routes_to_local_owner_not_completed_http(monkeypatch,tmp_path):
 import asyncio
 from delivery_api.runner import ProcessRunner
 from delivery_api.auth import Actor
 store=SQLiteStore(str(tmp_path/'architecture-decision.db'));task=store.create_task('Architecture choice','brief');tid=task['task_id']
 store.set_engine_session(tid,'completed-interview');store.update_phase(tid,'architecture','running')
 runner=ProcessRunner(store)
 class CompletedEngine:
  calls=0
  def snapshot(self,sid):
   self.calls+=1
   return EngineSnapshot(session_id=sid,task_id=tid,sequence=40,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE,open_input=None)
 engine=CompletedEngine()
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'runner',runner);monkeypatch.setattr(module,'engine_client',engine)
 async def go():
  decision={'decision_id':'system-baseline','prompt':'Choose baseline','options':[{'value':'greenfield','label':'Greenfield'}]}
  pending=asyncio.create_task(runner.request_decision(tid,'architecture',decision));await asyncio.sleep(0)
  result=await module.answer_interview(tid,module.InterviewAnswer(answer='greenfield',decision_id='system-baseline'),Actor('test','Founder'))
  return result,await pending
 result,choice=asyncio.run(go())
 assert result=={'status':'accepted'} and choice=='greenfield' and engine.calls==0
 assert any(e['kind']=='answer' and e['phase_key']=='architecture' and e['payload']['decision_id']=='system-baseline' for e in store.events(tid))


def test_architecture_baseline_rejects_stale_decision_without_http_fallback(monkeypatch,tmp_path):
 import asyncio
 from delivery_api.runner import ProcessRunner
 from delivery_api.auth import Actor
 from fastapi import HTTPException
 store=SQLiteStore(str(tmp_path/'stale-architecture.db'));task=store.create_task('Architecture choice','brief');tid=task['task_id']
 store.set_engine_session(tid,'completed-interview');store.update_phase(tid,'architecture','running')
 runner=ProcessRunner(store)
 class Engine:
  calls=0
  def snapshot(self,sid):self.calls+=1;raise AssertionError('completed HTTP session must not receive this decision')
 engine=Engine();monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'runner',runner);monkeypatch.setattr(module,'engine_client',engine)
 async def go():
  pending=asyncio.create_task(runner.request_decision(tid,'architecture',{'decision_id':'system-baseline','prompt':'Choose baseline'}));await asyncio.sleep(0)
  try:await module.answer_interview(tid,module.InterviewAnswer(answer='greenfield',decision_id='wrong-id'),Actor('test','Founder'))
  except HTTPException as exc:assert exc.status_code==409
  else:assert False,'stale decision was accepted'
  assert not pending.done()
  assert runner.input_state(tid)['waiting'] is True
  pending.cancel()
  try:await pending
  except asyncio.CancelledError:pass
 asyncio.run(go())
 assert engine.calls==0
 assert not any(e['kind']=='answer' for e in store.events(tid))
