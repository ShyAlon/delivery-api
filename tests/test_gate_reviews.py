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
 first=c.get(f'/api/tasks/{tid}/gates/gate-intent/review').json()['review']
 revision=first['revision']
 draft={'revision':revision,'choice':'Proceed','comment':'Review this','semantic_decisions':[{'item_id':'R-1','action':'replace','replacement_text':'{"statement":"saved"}'}]}
 saved=c.post(f'/api/tasks/{tid}/gates/gate-intent/drafts',json=draft);assert saved.status_code==200
 response=c.get(f'/api/tasks/{tid}/gates/gate-intent/review',params={'revision':revision,'draft':saved.json()['draft_id']}).json()
 assert response['review']==first
 restored=response['draft']
 assert restored['semantic_decisions']==draft['semantic_decisions'];assert restored['comment']=='Review this'


def test_gate_review_rejects_conflicting_retained_evidence(tmp_path,monkeypatch):
 import copy
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch);store.wait_at_gate(tid,'intent')
 first=c.get(f'/api/tasks/{tid}/gates/gate-intent/review').json()['review']
 original=store.decision_review
 for field in ('documents','artifacts'):
  changed=copy.deepcopy(first)
  if field=='documents':changed['documents']=[{'name':'fake','view':{'content':'changed'}}]
  else:changed['task']['artifacts']=[{'phase_key':'intent','name':'fake','stale':False}]
  monkeypatch.setattr(store,'decision_review',lambda *args,**kwargs:changed)
  response=c.get(f'/api/tasks/{tid}/gates/gate-intent/review')
  assert response.status_code==409,response.text
 monkeypatch.setattr(store,'decision_review',original)
 assert store.decision_review(tid,'gate:gate-intent',first['revision'])==first

def test_missing_semantic_review_fails_closed_and_restore_recovers(tmp_path,monkeypatch):
 # Integrity: every srs run produces its semantic review, so a session-recorded
 # task at gate-srs REQUIRES srs.semantic-review.json locally. A fresh host
 # before restore/backfill must be refused with a recoverable missing-evidence
 # error - never silently approve without the reviewed-write evidence.
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':'session-one','intent_model_ref':'session-one','purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 path=artifacts_root(str(tmp_path/'artifacts'))/'session-one'/'spec';path.mkdir(parents=True);artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert denied.status_code==400 and 'Required approval evidence is missing' in denied.text,denied.text
 # Recovery: the companion lands (restore/backfill); approval proceeds.
 semantic=make_review('session-one','srs',body,[]);(path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 store.set_model_default(tid,'deepseek:deepseek-flash')
 async def no_continue(*args,**kwargs): pass
 monkeypatch.setattr(module.orchestrator,'continue_after',no_continue)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert reply.status_code==200,reply.text


def _seed_srs_gate(tmp_path, monkeypatch):
 """Session-recorded task waiting at gate-srs with canonical artifacts on disk."""
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch)
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':'session-one','intent_model_ref':'session-one','purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 path=artifacts_root(str(tmp_path/'artifacts'))/'session-one'/'spec';path.mkdir(parents=True)
 artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 semantic=make_review('session-one','srs',body,[]);(path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 store.set_model_default(tid,'deepseek:deepseek-flash')
 async def no_continue(*args,**kwargs): pass
 monkeypatch.setattr(module.orchestrator,'continue_after',no_continue)
 return store,tid,c,path,body,semantic

def _seed_lineage(tid, store, spec, phase, name, settled):
 from spec_generator import lineage
 generation=store.task(tid)['generation']
 lineage.bind(spec,task_id=tid,session_id='session-one',generation=generation,
              phase=phase,name=name,sha256=lineage.sha256_file(spec/name),
              source='stage-commit',settled=settled)

def test_gate_approval_flips_lineage_bindings_settled(tmp_path,monkeypatch):
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch)
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=False)
 _seed_lineage(tid,store,spec,'srs','srs.semantic-review.json',settled=False)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert reply.status_code==200,reply.text
 record=lineage.load(spec)
 assert lineage.lookup(record,'srs','srs.json')['settled'] is True,'approval settles the phase binding'
 assert lineage.lookup(record,'srs','srs.semantic-review.json')['settled'] is True

def test_gate_redo_invalidates_redone_phase_and_downstream_only(tmp_path,monkeypatch):
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch)
 (spec/'needs.json').write_text('{"needs":[]}')
 (spec/'architecture.json').write_text('{"session_id":"session-one"}')
 _seed_lineage(tid,store,spec,'needs','needs.json',settled=True)
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=True)
 _seed_lineage(tid,store,spec,'architecture','architecture.json',settled=False)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'redo','steer':'regenerate with tighter scope'})
 assert reply.status_code==200,reply.text
 record=lineage.load(spec)
 assert lineage.lookup(record,'srs','srs.json') is None,'redone phase binding dropped'
 assert lineage.lookup(record,'architecture','architecture.json') is None,'downstream binding dropped'
 assert lineage.lookup(record,'needs','needs.json')['settled'] is True,'upstream settled work untouched'

def test_semantic_decisions_rebind_the_new_bytes(tmp_path,monkeypatch):
 import json
 from vmodel_contracts.semantic_review import make_review
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch)
 # replace the empty review with one carrying a finding so a decision is required
 flagged=make_review('session-one','srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Specifies a widget','original':body['functional'][0]}])
 (spec/'srs.semantic-review.json').write_text(flagged.model_dump_json())
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=False)
 _seed_lineage(tid,store,spec,'srs','srs.semantic-review.json',settled=False)
 accepted=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':flagged.revision,'decisions':[{'item_id':'R-1','action':'accept'}]})
 assert accepted.status_code==200,accepted.text
 record=lineage.load(spec)
 entry=lineage.lookup(record,'srs','srs.json')
 assert entry['sha256']==lineage.sha256_file(spec/'srs.json'),'binding tracks the reviewed write'
 assert entry['source']=='founder-decision' and entry['settled'] is False,'decision binds provisional until gate approval'
 companion=lineage.lookup(record,'srs','srs.semantic-review.json')
 assert companion['sha256']==lineage.sha256_file(spec/'srs.semantic-review.json')
 # and the re-bound record still verifies for resume hydration
 lineage.verify_file(record,phase='srs',name='srs.json',path=spec/'srs.json',
                     task_id=tid,session_id='session-one',generation=store.task(tid)['generation'])
