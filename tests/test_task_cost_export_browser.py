"""Real signed Everything download, owned accounting and browser ZIP scrub path."""
import io,json,zipfile
from uuid import uuid4
from delivery_api.tenancy import tenant_context
from playwright.sync_api import expect


def test_everything_download_contains_numeric_accounting_and_no_secrets(pg_browser,tmp_path):
 page,base,w=pg_browser
 with tenant_context(w.scope):
  tid=w.store.create_task('Export evidence fixture '+uuid4().hex)['task_id']
  w.store.set_engine_session(tid,str(uuid4()));job=w.store.enqueue_worker(tid,'phase:intent');w.store.claim_worker(job)
 with w.admin() as c:
  c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1000000000,1000000000,0)',(w.scope.owner_id,))
 from test_answer_accounting_db import begin,finish,usage
 u=usage(tokens=321);u['attempts'][0]['attempt_id']='export-browser-'+uuid4().hex
 begin(w,job,aid='export-browser-'+uuid4().hex);answer=None
 with w.admin() as c:answer=c.execute('SELECT answer_id FROM answer_charge_receipts WHERE job_id=%s',(job,)).fetchone()[0]
 finish(w,aid=answer,u=u)
 page.goto(base+'/tasks/'+tid+'/view')
 # Use actual collectors and ZIP/scrubber, no fake HTTP response for cost evidence.
 result=page.evaluate('''async tid=>{const {collectTaskCosts}=await import('/assets/debug-package.js');return await collectTaskCosts({taskId:tid,api:async path=>{const r=await fetch(path);if(!r.ok)throw Error(r.status);return r.json()}})}''',tid)
 assert result['complete'] and len(result['sections'])==4,result
 path=tmp_path/'export-fixture.json';path.write_text(json.dumps(result))
 # The export endpoint is real even without historical rows. Numeric hostile
 # fixture exercises the final identical scrubPart -> zipText output path.
 with page.expect_download() as info:
  page.evaluate('''async data=>{const {scrubPart}=await import('/assets/log-scrub.js');const {zipText}=await import('/assets/zip.js');const text=scrubPart(JSON.stringify({...data,input_tokens:321,output_tokens:45,access_token:'secret-fixture',answer_id:'a'.repeat(64)}));const blob=new Blob([zipText('requi-everything.txt',text)],{type:'application/zip'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='requi-everything.zip';a.click()}''',result)
 download=info.value;target=tmp_path/'requi-everything.zip';download.save_as(target)
 with zipfile.ZipFile(target) as z:
  raw=z.read('requi-everything.txt');data=json.loads(raw)
 assert data['input_tokens']==321 and data['output_tokens']==45 and data['answer_id']=='a'*64
 assert b'secret-fixture' not in raw and data['complete']
 # Exercise the actual Debug button and final Everything ZIP, not only helpers.
 page.locator('#accountbutton').click();page.get_by_role('menuitem',name='Debug',exact=True).click()
 with page.expect_download() as actual:
  page.locator('#downloadeverything').click()
 target=tmp_path/'actual-everything.zip';actual.value.save_as(target)
 with zipfile.ZipFile(target) as z:text=z.read('requi-everything.txt').decode()
 section=text.split('===== Full task call and cost evidence =====\n',1)[1].split('\n\n=====',1)[0]
 data=json.loads(section);assert data['complete'] and len(data['sections'])==4
 row=next(x for x in data['sections'] if x['source']=='answer_usage')['rows'][0]
 assert row['input_tokens']==321 and row['answer_id']==answer and row['actual_retail_credits'] is not None
 assert row['provider_expense'] is None and row['logical_call_id'] is None
 expect(page.locator('#debugstatus')).to_contain_text('Downloaded requi-everything.zip')
 page.screenshot(path='/tmp/call-export-proof.png',full_page=False)
