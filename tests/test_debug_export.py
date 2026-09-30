import io,json,zipfile
from pathlib import Path
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
