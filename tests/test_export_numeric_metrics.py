"""Usage counts are diagnostics, not credentials. Names alone grant no exemption."""
import math
import pytest
from delivery_api.debug_export import redact

@pytest.mark.parametrize('key',['input_tokens','output_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','tokens_per_s','prompt_eval_count','eval_count'])
def test_numeric_metric_is_preserved_but_never_secret_shaped_values(key):
 assert redact({key:123})=={key:123}
 # Null is explicit unknown evidence, never a credential. Preserve it rather than falsely labeling it redacted.
 assert redact({key:None})=={key:None}
 for value in ('sk-secret-fixture',True,{'access_token':'secret-fixture'},['secret-fixture'],float('nan'),float('inf'),-1):
  assert redact({key:value})=={key:'[REDACTED]'},(key,value)


def test_not_a_general_token_name_exemption():
 assert redact({'access_token':123,'refresh_token':123,'token':123,'input_tokens_password':123})=={
  'access_token':'[REDACTED]','refresh_token':'[REDACTED]','token':'[REDACTED]','input_tokens_password':'[REDACTED]'}


def test_plain_log_metrics_preserved_and_credentials_still_removed():
 text='input_tokens=123 output_tokens=45 tokens_per_s=2.5 api_key=secret-fixture access_token=secret-fixture'
 out=redact(text)
 assert 'input_tokens=123' in out and 'output_tokens=45' in out and 'tokens_per_s=2.5' in out
 assert 'secret-fixture' not in out


def test_browser_scrubber_matches_schema_and_text():
 import json,subprocess
 from pathlib import Path
 module=Path('src/delivery_api/web/log-scrub.js').resolve().as_uri()
 code="import {scrubJson,scrubText} from "+json.dumps(module)+";console.log(JSON.stringify([scrubJson({input_tokens:123,output_tokens:'secret-fixture',eval_count:true,access_token:123,tokens_per_s:2.5}),scrubText('input_tokens=123 api_key=secret-fixture')]));"
 result=subprocess.run(['node','--input-type=module','-e',code],text=True,capture_output=True)
 assert result.returncode==0,result.stderr
 obj,text=json.loads(result.stdout)
 assert obj=={'input_tokens':123,'output_tokens':'[redacted]','eval_count':'[redacted]','access_token':'[redacted]','tokens_per_s':2.5}
 assert 'input_tokens=123' in text and 'secret-fixture' not in text


def test_join_ids_survive_json_scrub_for_cost_attribution():
 import subprocess,json
 from pathlib import Path
 module=Path('src/delivery_api/web/log-scrub.js').resolve().as_uri()
 value={'answer_id':'a'*64,'attempt_id':'b'*32,'job_id':'job-fixture','access_token':'c'*64}
 result=subprocess.run(['node','--input-type=module','-e',"import {scrubJson} from "+json.dumps(module)+";console.log(JSON.stringify(scrubJson("+json.dumps(value)+")));"],text=True,capture_output=True)
 assert result.returncode==0,result.stderr
 assert json.loads(result.stdout)=={**value,'access_token':'[redacted]'}


def test_client_cost_collector_keeps_failed_source_partial_and_continues():
 import subprocess,json
 from pathlib import Path
 module=Path('src/delivery_api/web/debug-package.js').resolve().as_uri()
 code="""import {collectTaskCosts} from MODULE;
 const r=await collectTaskCosts({taskId:'t',api:async path=>{const p=new URL(path,'http://test');const source=p.searchParams.get('source');if(source==='answer_usage')throw Error('offline');return {task_id:'t',source,rows:[],complete:true}}});console.log(JSON.stringify(r));""".replace('MODULE',json.dumps(module))
 r=subprocess.run(['node','--input-type=module','-e',code],text=True,capture_output=True)
 assert r.returncode==0,r.stderr
 out=json.loads(r.stdout);assert not out['complete'] and len(out['sections'])==4
 assert out['sections'][1]['availability']=='unavailable' and out['sections'][-1]['complete']


@pytest.mark.parametrize('key',['prompt_tokens','completion_tokens','reasoning_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','cached_tokens'])
def test_provider_native_numeric_counts_survive_both_scrubbers(key):
 import subprocess,json
 from pathlib import Path
 value={key:123,'access_token':'secret-fixture','logical_call_id':'call:'+'a'*64,'snapshot_sha256':'b'*64}
 assert redact(value)=={**value,'access_token':'[REDACTED]'}
 module=Path('src/delivery_api/web/log-scrub.js').resolve().as_uri()
 result=subprocess.run(['node','--input-type=module','-e','import {scrubJson} from '+json.dumps(module)+';console.log(JSON.stringify(scrubJson('+json.dumps(value)+')));'],text=True,capture_output=True)
 assert result.returncode==0,result.stderr
 assert json.loads(result.stdout)=={**value,'access_token':'[redacted]'}
 for v in ('secret-fixture',True,{'access_token':'secret-fixture'}):assert redact({key:v})=={key:'[REDACTED]'}


def test_unknown_token_detail_is_null_not_falsely_redacted():
 import subprocess,json
 from pathlib import Path
 value={'reasoning_tokens':None,'cache_hit_tokens':None,'access_token':None}
 assert redact(value)=={'reasoning_tokens':None,'cache_hit_tokens':None,'access_token':'[REDACTED]'}
 module=Path('src/delivery_api/web/log-scrub.js').resolve().as_uri()
 result=subprocess.run(['node','--input-type=module','-e','import {scrubJson,scrubText} from '+json.dumps(module)+';console.log(JSON.stringify([scrubJson('+json.dumps(value)+'),scrubText(\'reasoning_tokens=null access_token=null\')]));'],text=True,capture_output=True)
 assert result.returncode==0,result.stderr
 assert json.loads(result.stdout)==[{'reasoning_tokens':None,'cache_hit_tokens':None,'access_token':'[redacted]'},'reasoning_tokens=null access_token=[redacted]']
 assert redact('reasoning_tokens=null access_token=null')=='reasoning_tokens=null access_token=[REDACTED]'
