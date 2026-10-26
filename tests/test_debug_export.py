import io,json,zipfile
from delivery_api.debug_export import diagnostic_zip,redact

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
  assert any(x['reason']=='not recorded' for x in manifest['omissions'])
  assert not any('checkpoint' in x for x in z.namelist())

def test_outside_symlink_and_oversize_source_are_omitted(tmp_path):
 root=tmp_path/'session';(root/'interview').mkdir(parents=True);outside=tmp_path/'private';outside.write_text('private');(root/'interview'/'run.log').symlink_to(outside)
 (root/'interview'/'llm-calls.jsonl').write_text('x'*2_000_001)
 raw=diagnostic_zip(root,'t','s',1,{}, {},[],tmp_path/'absent')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  reasons={x['reason'] for x in json.loads(z.read('manifest.json'))['omissions']}
  assert 'outside run root' in reasons and 'source over 2 MB limit' in reasons

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
  assert {'source':'interview/verification.jsonl','reason':'not recorded'} in json.loads(z.read('manifest.json'))['omissions']

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
