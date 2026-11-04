import io,json,zipfile
from delivery_api.debug_export import diagnostic_zip as _diagnostic_zip,redact
import pytest

@pytest.fixture(autouse=True)
def pg_export_reader(pg_scoped_workspace,monkeypatch):
 from delivery_api.generator_documents import GeneratorDocuments
 store=pg_scoped_workspace.store;tid=store.create_task('Diagnostic evidence')['task_id']
 store.set_engine_session(tid,__import__('uuid').uuid4().hex)
 job=store.enqueue_worker(tid,'phase:needs');docs=GeneratorDocuments(store,store.claim_worker(job),job)
 def export(root,*args,**kwargs):
  # Seed only ordinary fixture files; never import a symlink or consult it in export.
  for area in ('interview','spec'):
   for name in ('run.log','llm-calls.jsonl','debug-http.jsonl','usage.jsonl','verification.jsonl'):
    path=root/area/name
    if path.is_file() and not path.is_symlink():docs.write(area+'/'+name,path.read_bytes())
  return _diagnostic_zip(root,*args,**kwargs,document_reader=docs.read)
 monkeypatch.setitem(globals(),'diagnostic_zip',export)

diagnostic_zip=_diagnostic_zip

def test_debug_zip_is_scoped_bounded_and_secret_redacted(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True);(root/'spec').mkdir()
 (root/'interview'/'llm-calls.jsonl').write_text(json.dumps({'provider':'test','api_key':'super-secret','request':{'Authorization':'Bearer secret','prompt':'Project context'}})+'\n')
 (root/'spec'/'llm-calls.jsonl').write_text(json.dumps({'stage':'architecture','response':'expected project output'})+'\n')
 log=tmp_path/'run.log';log.write_text("stamp | task_id='task-a' password=private\nstamp | task_id='other' private project\n")
 raw=diagnostic_zip(root,'task-a','session',2,{'started_at':'start','ended_at':'end','records':[{'message':'Bearer topsecret'}]}, {'version':2},[],log)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  manifest=json.loads(z.read('manifest.json'));assert manifest['generation']==2
  assert 'interview/llm-calls.jsonl' in z.namelist() and 'spec/llm-calls.jsonl' in z.namelist()
  assert b'super-secret' not in z.read('interview/llm-calls.jsonl');assert b'secret' not in z.read('interview/llm-calls.jsonl')
  assert b'Project context' in z.read('interview/llm-calls.jsonl')
  assert b'topsecret' not in z.read('browser.json');assert b'other' not in z.read('run.log');assert b'private' not in z.read('run.log')
  assert any(x['reason']=='not recorded in owned PG documents' for x in manifest['omissions'])
  assert not any('checkpoint' in x for x in z.namelist())

def test_outside_symlink_and_oversize_source_are_omitted(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True);outside=tmp_path/'private';outside.write_text('private');(root/'interview'/'run.log').symlink_to(outside)
 (root/'interview'/'llm-calls.jsonl').write_text('x'*2_000_001)
 raw=diagnostic_zip(root,'t','s',1,{}, {},[],tmp_path/'absent')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  reasons={x['reason'] for x in json.loads(z.read('manifest.json'))['omissions']}
  assert 'not recorded in owned PG documents' in reasons and 'source over 2 MB limit' in reasons

def test_secrets_in_nested_text_are_redacted():
 value=redact({'password':'abc','nested':[{'message':'Authorization: Bearer abc'}],'ordinary':'Keep project data'})
 assert value['password']=='[REDACTED]';assert 'abc' not in str(value);assert value['ordinary']=='Keep project data'

def test_verification_scores_are_in_the_zip_for_calibration(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True)
 rows=[{'ts':1.0,'turn':3,'target_area':'data','passed':False,'failed_checks':['grounding'],
  'grounding':{'score':0,'span':None,'result':'no_overlap','passed':False},'implementation_hit':False,'founder_role_hit':False,
  'retrieval_spans':[{'id':'t2','score':2}],'proposed_question':'Which payment currencies matter most?',
  'final_question':'Which payment currencies matter most?','replaced':False,'non_answer':False,'retry_count':0,'fallback_used':False},
  {'ts':2.0,'turn':4,'target_area':'flows','passed':True,'failed_checks':[],'retry_count':1,'fallback_used':True,'api_key':'super-secret'}]
 (root/'interview'/'verification.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\nnot json\n')
 raw=diagnostic_zip(root,'t','s',1,{}, {},[],tmp_path/'absent')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  manifest=json.loads(z.read('manifest.json'));assert 'interview/verification.jsonl' in manifest['included']
  got=json.loads(z.read('interview/verification.jsonl'))
  assert [r.get('turn') for r in got]==[3,4,None] and got[0]['grounding']['result']=='no_overlap'
  assert got[1]['fallback_used'] is True and got[2]=={'unparsed':'not json'}
  assert b'super-secret' not in z.read('interview/verification.jsonl')
  assert not any(x['source']=='spec/verification.jsonl' for x in manifest['omissions'])

def test_missing_verification_scores_are_listed_not_hidden(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True)
 raw=diagnostic_zip(root,'t','s',1,{}, {},[],tmp_path/'absent')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  assert {'source':'interview/verification.jsonl','reason':'not recorded in owned PG documents'} in json.loads(z.read('manifest.json'))['omissions']

def test_engine_log_tail_is_in_the_zip_filtered_to_the_task(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True)
 sid='11111111-2222-3333-4444-555555555555'
 engine=tmp_path/'engine.log'
 engine.write_text("2026-10-26T06:25:28Z INFO pg.connect.end session=%s dur_ms=4\n"
                   "2026-10-26T06:25:28Z ERROR interview session start failed task=task-a\n"
                   "2026-10-26T06:25:29Z INFO pg.connect.end session=99999999-8888-7777-6666-555555555555 dur_ms=3\n"%sid)
 raw=diagnostic_zip(root,'task-a',sid,1,{}, {},[],tmp_path/'absent-run.log',engine)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  assert 'engine.log' in z.namelist()
  text=z.read('engine.log').decode()
  assert 'pg.connect.end session=%s'%sid in text and 'task=task-a' in text
  assert '99999999' not in text

def test_absent_engine_log_is_an_omission_not_a_failure(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True)
 raw=diagnostic_zip(root,'t','s',1,{}, {},[],tmp_path/'absent-run.log',tmp_path/'absent-engine.log')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  omissions=json.loads(z.read('manifest.json'))['omissions']
  assert any(x['source']=='engine.log' and x['reason']=='not recorded' for x in omissions)


def test_log_scope_requires_exact_field_and_value_not_prefix_or_message(tmp_path):
 root=tmp_path/'run';root.mkdir()
 source=tmp_path/'engine.log'
 accepted=["ERROR task=task-a failure", "ERROR task_id='task-a' failure", 'ERROR task="task-a" failure',
           "ERROR session_id=sid-a failed", "ERROR session='sid-a' failed"]
 rejected=["ERROR task=task-a-other neighboring", "ERROR task=task-a/other neighboring",
           "ERROR task='task-a-other' neighboring", "ERROR other_task=task-a neighboring",
           "ERROR session=sid-a-other neighboring", "ERROR task=other message='task=task-a' neighboring",
           "ERROR task=other\n    task=task-a neighboring continuation"]
 source.write_text('\n'.join(accepted+rejected)+'\n')
 raw=diagnostic_zip(root,'task-a','sid-a',1,{}, {},[],source,source)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  for name in ['run.log','engine.log']:
   text=z.read(name).decode()
   assert all(line in text for line in accepted)
   assert 'neighboring' not in text


def test_log_scope_treats_ids_as_literals(tmp_path):
 root=tmp_path/'run';root.mkdir();source=tmp_path/'engine.log'
 source.write_text('INFO task=other no-match\nERROR task=a.b-1 own\nERROR task=axb-1 neighboring\n')
 raw=diagnostic_zip(root,'a.b-1','s',1,{}, {},[],source)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  assert 'own' in z.read('run.log').decode() and 'neighboring' not in z.read('run.log').decode()


def test_log_scope_refuses_conflicting_and_malformed_fields():
 from delivery_api.debug_export import _owned_log_line
 assert not _owned_log_line("task=task-a task_id=other",'task-a','s')
 assert not _owned_log_line("task=task-a session=other",'task-a','s')
 assert not _owned_log_line("task='task-a",'task-a','s')
 assert not _owned_log_line("    task=task-a",'task-a','s')
 assert _owned_log_line("ERROR task=task-a session=s",'task-a','s')


def test_bounded_tail_never_promotes_partial_record(tmp_path):
 root=tmp_path/'run';root.mkdir();source=tmp_path/'engine.log'
 source.write_text('x'*100+" task=task-a neighboring\n"+'x'*1_999_950+"\nERROR task=task-a own\n")
 raw=diagnostic_zip(root,'task-a','s',1,{}, {},[],source)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  assert 'own' in z.read('run.log').decode()
  assert 'neighboring' not in z.read('run.log').decode()
