import pytest
from tests.test_decision_pages import setup

def _case_gate_content_revision_comments_drafts_and_history(tmp_path,monkeypatch,pg_scoped_workspace):
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace)
 store.wait_at_gate(tid,'intent')
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'intent')/'intent_model.json';path.write_text('{"problem_statement":"First evidence"}')
 monkeypatch.setattr(__import__('delivery_api.app',fromlist=['x']),'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'intent','name':'intent_model.json','ref':str(path),'present':True,'valid':True,'stale':False,'generation':1}])
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

def _case_semantic_srs_choices_block_approval_then_structurally_validate(tmp_path,monkeypatch,pg_scoped_workspace):
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace);sid=engine.session_id
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body={'session_id':sid,'intent_model_ref':sid,'purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}
 body=SRSDocument.model_validate(body).model_dump(mode='json')
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'srs');store.update_phase(tid,'srs','awaiting_approval');artifact=path/'srs.json';artifact.write_text(json.dumps(body));artifact.with_suffix('.md').write_text('STALE MARKDOWN')
 semantic=make_review(sid,'srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Specifies a widget','original':body['functional'][0]}])
 (path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 pending=c.get(f'/api/tasks/{tid}/gates/gate-srs/review').json();assert pending['review']['semantic_review']['findings']
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'});assert denied.status_code==400
 invalid=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':semantic.revision,'decisions':[{'item_id':'R-1','action':'replace','replacement':{'statement':'missing IDs'}}]});assert invalid.status_code==422
 accepted=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':semantic.revision,'decisions':[{'item_id':'R-1','action':'accept'}]});assert accepted.status_code==200
 assert not artifact.with_suffix('.md').exists();assert json.loads(artifact.read_text())['functional'][0]['statement']==body['functional'][0]['statement']
 stored=json.loads((path/'srs.semantic-review.json').read_text());assert stored['decisions'][0]['actor']=='Signed-In User'


def _case_interrupted_semantic_write_blocks_approval_and_same_choices_repair(tmp_path,monkeypatch,pg_scoped_workspace):
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace);sid=engine.session_id;monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'));store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':sid,'intent_model_ref':sid,'purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'srs');store.update_phase(tid,'srs','awaiting_approval');artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 r=make_review(sid,'srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Widget','original':body['functional'][0]}]);replacement={**body['functional'][0],'statement':'The system shall display the saved status.'}
 decisions=[{'item_id':'R-1','action':'replace','replacement':replacement,'actor':'local user'}]
 recorded={**r.model_dump(),'decisions':decisions};(path/'srs.semantic-review.json').write_text(json.dumps(recorded))
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'});assert denied.status_code==400 and 'incomplete' in denied.text
 repaired=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':r.revision,'decisions':decisions});assert repaired.status_code==200,repaired.text
 assert json.loads(artifact.read_text())['functional'][0]['statement']==replacement['statement']

def _case_gate_semantic_draft_restores_exact_saved_fields(tmp_path,monkeypatch,pg_scoped_workspace):
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace)
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'intent')/'intent_model.json';path.write_text('{"problem_statement":"Exact evidence"}')
 import delivery_api.app as module
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'intent','name':'intent_model.json','ref':str(path),'present':True,'valid':True,'stale':False,'generation':1}])
 first=c.get(f'/api/tasks/{tid}/gates/gate-intent/review').json()['review']
 revision=first['revision']
 draft={'revision':revision,'choice':'Proceed','comment':'Review this','semantic_decisions':[{'item_id':'R-1','action':'replace','replacement_text':'{"statement":"saved"}'}]}
 saved=c.post(f'/api/tasks/{tid}/gates/gate-intent/drafts',json=draft);assert saved.status_code==200
 response=c.get(f'/api/tasks/{tid}/gates/gate-intent/review',params={'revision':revision,'draft':saved.json()['draft_id']}).json()
 assert response['review']==first
 restored=response['draft']
 assert restored['semantic_decisions']==draft['semantic_decisions'];assert restored['comment']=='Review this'


def _case_gate_review_rejects_conflicting_retained_evidence(tmp_path,monkeypatch,pg_scoped_workspace):
 import copy
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace)
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

def _case_missing_semantic_review_fails_closed_and_restore_recovers(tmp_path,monkeypatch,pg_scoped_workspace):
 # Integrity: every srs run produces its semantic review, so a session-recorded
 # task at gate-srs REQUIRES srs.semantic-review.json locally. A fresh host
 # before restore/backfill must be refused with a recoverable missing-evidence
 # error - never silently approve without the reviewed-write evidence.
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace);sid=engine.session_id
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':sid,'intent_model_ref':sid,'purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'srs');store.update_phase(tid,'srs','awaiting_approval');artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert denied.status_code==400 and 'Required approval evidence is missing' in denied.text,denied.text
 # Recovery: the companion lands (restore/backfill); approval proceeds.
 semantic=make_review(sid,'srs',body,[]);(path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 store.set_model_default(tid,'deepseek:deepseek-flash')
 async def no_continue(*args,**kwargs): pass
 monkeypatch.setattr(module.orchestrator,'continue_after',no_continue)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert reply.status_code==200,reply.text


def _seed_srs_gate(tmp_path, monkeypatch,pg_scoped_workspace):
 """Session-recorded task waiting at gate-srs with canonical artifacts on disk."""
 import json
 from vmodel_contracts import SRSDocument
 from vmodel_contracts.semantic_review import make_review
 from vmodel_contracts.runtime import artifacts_root
 import delivery_api.app as module
 store,tid,engine,c=setup(tmp_path,monkeypatch,pg_scoped_workspace);sid=engine.session_id
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
 store.wait_at_gate(tid,'srs')
 body=SRSDocument.model_validate({'session_id':sid,'intent_model_ref':sid,'purpose':'Test','scope_summary':'Test','functional':[{'requirement_id':'R-1','title':'Button','statement':'The system shall show a button.','priority':'must','kind':'functional','acceptance':[{'criterion_id':'AC-1','statement':'Click button'}],'need_ids':['N-1'],'intent_fields':['problem_statement']}],'non_functional':[]}).model_dump(mode='json')
 from tests.test_stack_decisions import _PGSpec
 path=_PGSpec(store,tid,'srs');store.update_phase(tid,'srs','awaiting_approval')
 artifact=path/'srs.json';artifact.write_text(json.dumps(body))
 semantic=make_review(sid,'srs',body,[]);(path/'srs.semantic-review.json').write_text(semantic.model_dump_json())
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda snapshot_task_id, **kwargs:[{'phase_key':'srs','name':'srs.json','ref':str(artifact),'present':True,'valid':True,'stale':False,'generation':1}])
 store.set_model_default(tid,'deepseek:deepseek-flash')
 async def no_continue(*args,**kwargs): pass
 monkeypatch.setattr(module.orchestrator,'continue_after',no_continue)
 return store,tid,c,path,body,semantic

def _seed_lineage(tid, store, spec, phase, name, settled):
 import hashlib,json
 from delivery_api.generator_documents import GeneratorDocuments
 reader=GeneratorDocuments.owner_reader(store,tid)
 raw=reader.read('spec/lineage.json')
 record=json.loads(raw) if raw else {'version':1,'entries':[],'inputs':{}}
 record['entries'].append({'phase':phase,'name':name,'sha256':hashlib.sha256((spec/name).read_bytes()).hexdigest(),'settled':settled,'source':'stage-commit','task_id':tid,'session_id':store.task(tid)['engine_session_id'],'generation':str(store.task(tid)['generation'])})
 (spec/'lineage.json').write_text(json.dumps(record))


def _case_gate_approval_flips_lineage_bindings_settled(tmp_path,monkeypatch,pg_scoped_workspace):
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch,pg_scoped_workspace)
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=False)
 _seed_lineage(tid,store,spec,'srs','srs.semantic-review.json',settled=False)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'approve','selected_option':'Proceed'})
 assert reply.status_code==200,reply.text
 record=__import__('json').loads((spec/'lineage.json').read_text())
 assert lineage.lookup(record,'srs','srs.json')['settled'] is True,'approval settles the phase binding'
 assert lineage.lookup(record,'srs','srs.semantic-review.json')['settled'] is True


def _case_gate_redo_invalidates_redone_phase_and_downstream_only(tmp_path,monkeypatch,pg_scoped_workspace):
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch,pg_scoped_workspace)
 (spec/'needs.json').write_text('{"needs":[]}')
 (spec/'architecture.json').write_text('{"session_id":"session-one"}')
 _seed_lineage(tid,store,spec,'needs','needs.json',settled=True)
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=True)
 _seed_lineage(tid,store,spec,'architecture','architecture.json',settled=False)
 reply=c.post(f'/api/tasks/{tid}/gates/gate-srs/decide',json={'action':'redo','steer':'regenerate with tighter scope'})
 assert reply.status_code==200,reply.text
 record=__import__('json').loads((spec/'lineage.json').read_text())
 assert lineage.lookup(record,'srs','srs.json') is None,'redone phase binding dropped'
 assert lineage.lookup(record,'architecture','architecture.json') is None,'downstream binding dropped'
 assert lineage.lookup(record,'needs','needs.json')['settled'] is True,'upstream settled work untouched'


def _case_semantic_decisions_rebind_the_new_bytes(tmp_path,monkeypatch,pg_scoped_workspace):
 import json
 from vmodel_contracts.semantic_review import make_review
 from spec_generator import lineage
 store,tid,c,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch,pg_scoped_workspace)
 sid=store.task(tid)['engine_session_id']
 # replace the empty review with one carrying a finding so a decision is required
 flagged=make_review(sid,'srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Specifies a widget','original':body['functional'][0]}])
 (spec/'srs.semantic-review.json').write_text(flagged.model_dump_json())
 _seed_lineage(tid,store,spec,'srs','srs.json',settled=False)
 _seed_lineage(tid,store,spec,'srs','srs.semantic-review.json',settled=False)
 accepted=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':flagged.revision,'decisions':[{'item_id':'R-1','action':'accept'}]})
 assert accepted.status_code==200,accepted.text
 record=__import__('json').loads((spec/'lineage.json').read_text())
 entry=lineage.lookup(record,'srs','srs.json')
 assert entry['sha256']==__import__('hashlib').sha256((spec/'srs.json').read_bytes()).hexdigest(),'binding tracks the reviewed write'
 assert entry['source']=='founder-decision' and entry['settled'] is False,'decision binds provisional until gate approval'
 companion=lineage.lookup(record,'srs','srs.semantic-review.json')
 assert companion['sha256']==__import__('hashlib').sha256((spec/'srs.semantic-review.json').read_bytes()).hexdigest()
 # and the re-bound record still verifies for resume hydration
 assert entry['task_id']==tid and entry['session_id']==sid and entry['generation']==str(store.task(tid)['generation'])
 assert entry['sha256']==__import__('hashlib').sha256((spec/'srs.json').read_bytes()).hexdigest()


def _case_lineage_rebind_import_rule(tmp_path,monkeypatch,pg_scoped_workspace):
 import inspect
 from delivery_api.generator_documents import transition_gate_lineage
 source=inspect.getsource(transition_gate_lineage)
 assert 'spec_generator' not in source and 'read_text' not in source
 assert 'lineage.json' in source

def _case_record_host_without_generator_refuses_before_any_mutation(tmp_path,monkeypatch,pg_scoped_workspace):
 # A malformed PG authority record must refuse BEFORE any artifact, manifest or
 # decision-record mutation. PG lineage no longer requires a filesystem module import - otherwise a refused decide would leave published
 # bytes drifted from the record. Assert bytes, gate state and events unchanged.
 import sys
 from vmodel_contracts.semantic_review import make_review
 store,tid,c,path,body,semantic=_seed_srs_gate(tmp_path,monkeypatch,pg_scoped_workspace)
 (path/'lineage.json').write_text('{"record_version":1,"session_id":"session-one"}')
 monkeypatch.setitem(sys.modules,'spec_generator',None)
 before=(path/'srs.json').read_bytes()
 denied=c.post(f'/api/tasks/{tid}/gates/gate-srs/semantic-review',json={'revision':semantic.revision,'decisions':[]})
 assert denied.status_code==409 and 'authority record' in denied.text,denied.text
 assert (path/'srs.json').read_bytes()==before
 gate=[g for g in store.task(tid)['gates'] if g['gate_key']=='gate-srs'][0]
 assert gate['state']=='waiting'
 assert not any(e['kind']=='semantic_review' for e in store.events(tid))


def _check_gate_content_revision_comments_drafts_and_history(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_gate_content_revision_comments_drafts_and_history
 _case_gate_content_revision_comments_drafts_and_history(tmp_path,monkeypatch,workspace)


def test_gate_content_revision_comments_drafts_and_history(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_gate_content_revision_comments_drafts_and_history(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_semantic_srs_choices_block_approval_then_structurally_validate(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_semantic_srs_choices_block_approval_then_structurally_validate
 _case_semantic_srs_choices_block_approval_then_structurally_validate(tmp_path,monkeypatch,workspace)


def test_semantic_srs_choices_block_approval_then_structurally_validate(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_semantic_srs_choices_block_approval_then_structurally_validate(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_interrupted_semantic_write_blocks_approval_and_same_choices_repair(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_interrupted_semantic_write_blocks_approval_and_same_choices_repair
 _case_interrupted_semantic_write_blocks_approval_and_same_choices_repair(tmp_path,monkeypatch,workspace)


def test_interrupted_semantic_write_blocks_approval_and_same_choices_repair(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_interrupted_semantic_write_blocks_approval_and_same_choices_repair(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_gate_semantic_draft_restores_exact_saved_fields(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_gate_semantic_draft_restores_exact_saved_fields
 _case_gate_semantic_draft_restores_exact_saved_fields(tmp_path,monkeypatch,workspace)


def test_gate_semantic_draft_restores_exact_saved_fields(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_gate_semantic_draft_restores_exact_saved_fields(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_gate_review_rejects_conflicting_retained_evidence(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_gate_review_rejects_conflicting_retained_evidence
 _case_gate_review_rejects_conflicting_retained_evidence(tmp_path,monkeypatch,workspace)


def test_gate_review_rejects_conflicting_retained_evidence(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_gate_review_rejects_conflicting_retained_evidence(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_missing_semantic_review_fails_closed_and_restore_recovers(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_missing_semantic_review_fails_closed_and_restore_recovers
 _case_missing_semantic_review_fails_closed_and_restore_recovers(tmp_path,monkeypatch,workspace)


def test_missing_semantic_review_fails_closed_and_restore_recovers(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_missing_semantic_review_fails_closed_and_restore_recovers(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_gate_approval_flips_lineage_bindings_settled(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_gate_approval_flips_lineage_bindings_settled
 _case_gate_approval_flips_lineage_bindings_settled(tmp_path,monkeypatch,workspace)


@pytest.mark.producer_integration
def test_gate_approval_flips_lineage_bindings_settled(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_gate_approval_flips_lineage_bindings_settled(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_gate_redo_invalidates_redone_phase_and_downstream_only(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_gate_redo_invalidates_redone_phase_and_downstream_only
 _case_gate_redo_invalidates_redone_phase_and_downstream_only(tmp_path,monkeypatch,workspace)


@pytest.mark.producer_integration
def test_gate_redo_invalidates_redone_phase_and_downstream_only(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_gate_redo_invalidates_redone_phase_and_downstream_only(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_semantic_decisions_rebind_the_new_bytes(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_semantic_decisions_rebind_the_new_bytes
 _case_semantic_decisions_rebind_the_new_bytes(tmp_path,monkeypatch,workspace)


@pytest.mark.producer_integration
def test_semantic_decisions_rebind_the_new_bytes(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_semantic_decisions_rebind_the_new_bytes(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_lineage_rebind_import_rule(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_lineage_rebind_import_rule
 _case_lineage_rebind_import_rule(tmp_path,monkeypatch,workspace)


def test_lineage_rebind_import_rule(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_lineage_rebind_import_rule(tmp_path,monkeypatch,pg_scoped_workspace)


def _check_record_host_without_generator_refuses_before_any_mutation(monkeypatch,tmp_path,workspace):
 from tests.test_gate_reviews import _case_record_host_without_generator_refuses_before_any_mutation
 _case_record_host_without_generator_refuses_before_any_mutation(tmp_path,monkeypatch,workspace)


def test_record_host_without_generator_refuses_before_any_mutation(tmp_path,monkeypatch,pg_scoped_workspace,pg_task_app):
 async def queue_only(*args,**kwargs):return kwargs.get('queued_job_id')
 monkeypatch.setattr('delivery_api.tenant_jobs.dispatch_phase',queue_only)
 monkeypatch.setattr('delivery_api.app.cloud_execution_admitted',lambda:True)
 pg_scoped_workspace.signed_app=pg_task_app(pg_scoped_workspace)
 _case_record_host_without_generator_refuses_before_any_mutation(tmp_path,monkeypatch,pg_scoped_workspace)
