import json,subprocess
from pathlib import Path
WEB=Path(__file__).parents[1]/'src/delivery_api/web'


def test_operator_package_collects_existing_db_evidence_with_exact_task_and_failures():
 source=f"""
import {{collectDebugEvidence}} from '{WEB/'debug-package.js'}';
const calls=[];
const out=await collectDebugEvidence({{taskId:'task a',operator:true,cloud:true,api:async url=>{{calls.push(url);if(url.includes('task-logs'))throw new Error('private backend error');return {{ok:true}}}}}});
console.log(JSON.stringify({{out,calls}}));
"""
 r=subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 result=json.loads(r.stdout);calls=result['calls'];sections=result['out']['sections']
 assert '/api/operator/task-failure-diagnostics?task_id=task%20a&limit=20' in calls
 assert '/api/debug/evidence?task_id=task%20a' in calls
 assert any(x['availability']=='unavailable' and x['source']=='db-state-events' for x in sections)
 assert 'private backend error' not in json.dumps(result['out'])
 assert result['out']['task_id']=='task a'


def test_customer_collection_never_calls_operator_endpoints():
 source=f"""
import {{collectDebugEvidence}} from '{WEB/'debug-package.js'}';
const calls=[];const out=await collectDebugEvidence({{taskId:'a',operator:false,cloud:true,api:async url=>{{calls.push(url);return {{}}}}}});
console.log(JSON.stringify({{calls,out}}));
"""
 r=subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 result=json.loads(r.stdout)
 assert not any('/operator/' in x or '/debug/evidence' in x for x in result['calls'])
 assert any(x['availability']=='policy_excluded' for x in result['out']['sections'])


def test_server_log_task_filter_never_includes_neighboring_task(monkeypatch,file_log_evidence,pg_operator_log_reader):
 from delivery_api import app as m
 from types import SimpleNamespace
 reader,store=pg_operator_log_reader
 monkeypatch.setattr(m,'store',store)
 monkeypatch.setattr(m,'_operator_reader',lambda:reader)
 repo=file_log_evidence.repository
 repo.append('activity','INFO task=task-a own',task_id='task-a')
 repo.append('activity','INFO task=task-a-other neighbor',task_id='task-a-other')
 repo.append('activity','INFO system-wide')
 from delivery_api.process_logs import flush
 assert flush(5)
 got=m.server_log(30,SimpleNamespace(id='operator',admin=True),'task-a')
 assert 'INFO task=task-a own' in got['text'] and got['count']==1
 assert 'neighbor' not in got['text'] and 'system-wide' not in got['text']


def test_package_fresh_reads_do_not_depend_on_dialog_loading_state():
 source=f"""
import {{collectDebugEvidence}} from '{WEB/'debug-package.js'}';
const calls=[];
const out=await collectDebugEvidence({{taskId:'live',operator:true,cloud:true,api:async url=>{{calls.push(url);await new Promise(r=>setTimeout(r,5));if(url.includes('credit-history'))throw new Error('sensitive');return {{text:'fresh '+url}}}}}});
console.log(JSON.stringify({{calls,out}}));
"""
 r=subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 result=json.loads(r.stdout)
 required=['/api/account/ledger','/api/account/credit-history','/api/debug/preflight','/api/admin/operations/request-costs','/api/admin/operations/capacity']
 assert all(p in result['calls'] for p in required)
 sections={s['source']:s for s in result['out']['sections']}
 assert sections['credits']['data']['text']=='fresh /api/account/ledger'
 assert sections['credit-history']['availability']=='unavailable'
 assert 'sensitive' not in str(result['out'])
 # Package must never scrape placeholders or stale globals from the dialog.
 identity=(WEB/'identity.js').read_text()
 handler=identity.split("document.querySelector('#downloadeverything').onclick=",1)[1].split("{const lh=document.querySelector('#ledgerhost')",1)[0]
 assert "document.querySelector('#ledgerhost').textContent" not in handler
 assert 'window.__preflightText' not in handler and 'window.__requestCostsText' not in handler


def test_usage_unavailable_preserves_safe_reason_not_available():
 source=f"""
import {{collectDebugEvidence}} from '{WEB/'debug-package.js'}';
const out=await collectDebugEvidence({{taskId:'a',operator:true,cloud:true,api:async url=>url.includes('task-usage')?{{availability:'unavailable',category:'permission_missing',required_capability:'usage.read',reason:'Usage read is not configured for this operator.',next:'Run the owner-reviewed usage setup.'}}:{{}}}});
console.log(JSON.stringify(out.sections.find(x=>x.source==='db-usage')));
"""
 r=subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 result=json.loads(r.stdout)
 assert result['availability']=='unavailable'
 assert result['data']['required_capability']=='usage.read'
