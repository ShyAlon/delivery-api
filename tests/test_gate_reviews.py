from tests.test_decision_pages import setup

def test_gate_content_revision_comments_drafts_and_history(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch)
 store.wait_at_gate(tid,'intent')
 path=tmp_path/'intent_model.json';path.write_text('{"problem_statement":"First evidence"}')
 monkeypatch.setattr(__import__('delivery_api.app',fromlist=['x']),'_task_artifact_snapshot',lambda task:[{'phase_key':'intent','name':'intent_model.json','ref':str(path),'present':True,'valid':True,'stale':False,'generation':1}])
 first=c.get(f'/api/tasks/{tid}/gates/gate-intent/review').json();revision=first['review']['revision']
 assert first['actionable'];assert 'First evidence' in first['review']['documents'][0]['view']['content']
 c.post(f'/api/tasks/{tid}/gates/gate-intent/comments',json={'revision':revision,'comment':'Keep the source'}).raise_for_status()
 draft=c.post(f'/api/tasks/{tid}/gates/gate-intent/drafts',json={'revision':revision,'choice':'Proceed','comment':'saved note'}).json()['draft_id']
 path.write_text('{"problem_statement":"New evidence"}')
 old=c.get(f'/api/tasks/{tid}/gates/gate-intent/review',params={'revision':revision,'draft':draft}).json()
 assert not old['actionable'];assert 'First evidence' in old['review']['documents'][0]['view']['content'];assert old['draft']['comment']=='saved note'
 assert old['comments'][0]['comment']=='Keep the source'
 reply=c.post(f'/api/tasks/{tid}/gates/gate-intent/decide',json={'action':'approve','selected_option':'Proceed','review_revision':revision})
 assert reply.status_code==409
 assert not any(e['kind']=='answer' for e in store.events(tid))

def test_semantic_srs_choices_block_approval_then_structurally_validate(tmp_path,monkeypatch):
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body={'session_id':'session-one','intent_model_ref':'session-one','purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}
 body=SRSDocument.model_validate(body).model_dump(mode='json')
 path=artifacts_root(str(tmp_path/'artifacts'))/'session-one'/'spec';path.mkdir(parents=True);artifact=path/'srs.json';artifact.write_text(json.dumps(body));artifact.with_suffix('.md').write_text('STALE MARKDOWN')
 semantic=make_review('session-one','srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Specifies a widget','original':body['functional'][0]}])
 (path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 pending=c.get(f'/api/tasks/{tid}/gates/gate-srs/review').json();assert pending['review']['semantic_review']['findings']
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'});assert denied.status_code==400
 invalid=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':semantic.revision,'decisions':[{'item_id':'R-1','action':'replace','replacement':{'statement':'missing IDs'}}]});assert invalid.status_code==422
 accepted=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':semantic.revision,'decisions':[{'item_id':'R-1','action':'accept'}]});assert accepted.status_code==200
 assert not artifact.with_suffix('.md').exists();assert json.loads(artifact.read_text())['functional'][0]['statement']==body['functional'][0]['statement']
 stored=json.loads((path/'srs.semantic-review.json').read_text());assert stored['decisions'][0]['actor']=='local user'


def test_interrupted_semantic_write_blocks_approval_and_same_choices_repair(tmp_path,monkeypatch):
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'));store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':'session-one','intent_model_ref':'session-one','purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 path=artifacts_root(str(tmp_path/'artifacts'))/'session-one'/'spec';path.mkdir(parents=True);artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 r=make_review('session-one','srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Widget','original':body['functional'][0]}]);replacement={**body['functional'][0],'statement':'The system shall display the saved status.'}
 decisions=[{'item_id':'R-1','action':'replace','replacement':replacement,'actor':'local user'}]
 recorded={**r.model_dump(),'decisions':decisions};(path/'srs.semantic-review.json').write_text(json.dumps(recorded))
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'});assert denied.status_code==400 and 'incomplete' in denied.text
 repaired=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':r.revision,'decisions':decisions});assert repaired.status_code==200,repaired.text
 assert json.loads(artifact.read_text())['functional'][0]['statement']==replacement['statement']

def test_gate_semantic_draft_restores_exact_saved_fields(tmp_path,monkeypatch):
 store,tid,engine,c=setup(tmp_path,monkeypatch);store.wait_at_gate(tid,'intent')
 path=tmp_path/'intent_model.json';path.write_text('{"problem_statement":"Exact evidence"}')
 import delivery_api.app as module
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task:[{'phase_key':'intent','name':'intent_model.json','ref':str(path),'present':True,'valid':True,'stale':False,'generation':1}])
 revision=c.get(f'/api/tasks/{tid}/gates/gate-intent/review').json()['review']['revision']
 draft={'revision':revision,'choice':'Proceed','comment':'Review this','semantic_decisions':[{'item_id':'R-1','action':'replace','replacement_text':'{"statement":"saved"}'}]}
 saved=c.post(f'/api/tasks/{tid}/gates/gate-intent/drafts',json=draft);assert saved.status_code==200
 restored=c.get(f'/api/tasks/{tid}/gates/gate-intent/review',params={'revision':revision,'draft':saved.json()['draft_id']}).json()['draft']
 assert restored['semantic_decisions']==draft['semantic_decisions'];assert restored['comment']=='Review this'
