import hashlib,json
from uuid import uuid4
from fastapi.testclient import TestClient
from vmodel_contracts.interactive import *
from delivery_api.store import SQLiteStore
import delivery_api.app as module

def setup(tmp_path,monkeypatch):
 store=SQLiteStore(str(tmp_path/'reviews.db'));task=store.create_task('Operating concept','brief');tid=task['task_id'];store.set_engine_session(tid,'session-one');store.update_phase(tid,'conops','running')
 from tests.test_decision_pages_data import draft
 evidence={'conops_draft':draft()}
 digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 pending=InputRequest(input_id=uuid4(),kind=InputKind.CHOICE,prompt='Arbitrary prompt text',rationale_required=True,options=[InputOption(value='1',label='Native app',summary='Installed app'),InputOption(value='2',label='PWA',summary='Installable web product')],context={'decision_purpose':'conops_concept','evidence_version':1,'session_id':'session-one','evidence_revision':digest,'evidence':evidence})
 class Engine:
  def snapshot(self,sid):return EngineSnapshot(session_id=sid,task_id=tid,sequence=self.sequence,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=self.pending)
 engine=Engine();engine.pending=pending;engine.sequence=3
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',engine)
 return store,tid,engine,TestClient(module.app)

def test_matching_shell_route_and_retained_old_review(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch);wait=str(engine.pending.input_id)
 assert c.get(f'/tasks/{tid}/decisions/{wait}?step=concept').status_code==200
 data=c.get(f'/api/tasks/{tid}/decisions/{wait}').json();assert data['actionable'];revision=data['review']['revision']
 for i in range(510):store.add_event(tid,'progress','another event')
 engine.sequence=5;engine.pending=engine.pending.model_copy(update={'input_id':uuid4()})
 body=c.get(f'/api/tasks/{tid}/decisions/{wait}?revision={revision}').json()
 assert not body['actionable'];assert body['review']['wait_id']==wait;assert body['review']['revision']==revision
 assert body['review']['decision']['context']['evidence']==data['review']['decision']['context']['evidence']
 assert c.get(f'/api/tasks/{tid}/decisions/{wait}?revision=missing').status_code==404

def test_draft_identity_and_comment_are_not_answers(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch);wait=str(engine.pending.input_id)
 revision=c.get(f'/api/tasks/{tid}/decisions/{wait}').json()['review']['revision']
 draft=c.post(f'/api/tasks/{tid}/decisions/{wait}/drafts',json={'revision':revision,'choice':'2','rationale':'native hardware needed','comment':'verify offline'}).json()['draft_id']
 read=c.get(f'/api/tasks/{tid}/decisions/{wait}',params={'revision':revision,'draft':draft}).json()
 assert read['draft']['choice']=='2';assert read['draft']['rationale']=='native hardware needed'
 assert c.get(f'/api/tasks/{tid}/decisions/{wait}',params={'revision':revision,'draft':'unknown'}).status_code==404
 assert c.post(f'/api/tasks/{tid}/decisions/{wait}/drafts',json={'revision':revision,'choice':'999'}).status_code==422
 c.post(f'/api/tasks/{tid}/decisions/{wait}/comments',json={'revision':revision,'comment':'Annotation only'}).raise_for_status()
 assert engine.pending.input_id.hex.replace('-','')==wait.replace('-','')
 assert c.get(f'/api/tasks/{tid}/decisions/{wait}').json()['comments'][0]['comment']=='Annotation only'
 assert not any(e['kind']=='answer' for e in store.events(tid));assert any(e['kind']=='artifact_comment' for e in store.events(tid))

def test_purpose_and_evidence_must_be_explicit_and_bound(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch);wait=str(engine.pending.input_id)
 engine.pending=engine.pending.model_copy(update={'context':{}})
 assert c.get(f'/api/tasks/{tid}/decisions/{wait}').status_code==404
 engine.pending=engine.pending.model_copy(update={'context':{'decision_purpose':'conops_concept','evidence_version':1,'session_id':'wrong','evidence_revision':'bad','evidence':{'draft':1}}})
 assert c.get(f'/api/tasks/{tid}/decisions/{wait}').status_code==404

def test_stale_review_cannot_submit(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch);wait=str(engine.pending.input_id)
 revision=c.get(f'/api/tasks/{tid}/decisions/{wait}').json()['review']['revision'];engine.sequence=4
 reply=c.post(f'/api/tasks/{tid}/answer',json={'answer':'1 - good choice','decision_id':wait,'expected_sequence':3,'review_revision':revision})
 assert reply.status_code==409
 assert not any(e['kind']=='answer' for e in store.events(tid))

def test_semantic_findings_use_structured_page_not_free_text(tmp_path,monkeypatch):
 from vmodel_contracts.semantic_review import make_review
 store,tid,engine,c=setup(tmp_path,monkeypatch)
 candidate={'items':['original']}
 review=make_review('session-one','intent',candidate,[{'finding_id':'f1','item_id':'i1','path':['items',0],'rule':'meaning','reason':'Not grounded','original':'original'}]).model_dump(mode='json')
 evidence={'semantic_review':review};digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 engine.pending=engine.pending.model_copy(update={'kind':InputKind.TEXT,'options':[],'context':{'decision_purpose':'semantic_review','evidence_version':1,'session_id':'session-one','evidence_revision':digest,'evidence':evidence}})
 result=c.get(f'/api/tasks/{tid}/decisions/{engine.pending.input_id}').json()
 assert result['actionable'];assert result['review']['kind']=='semantic_review';assert result['review']['decision']['context']['evidence']==evidence
