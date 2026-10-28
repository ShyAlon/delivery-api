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


def test_server_log_task_filter_never_includes_neighboring_task(monkeypatch):
 from delivery_api import app as m
 from types import SimpleNamespace
 monkeypatch.setattr(m,'store',SimpleNamespace(task=lambda t:{'engine_session_id':'s'}))
 monkeypatch.setattr(m.server_log_buffer,'read',lambda *a:{'text':"INFO task=task-a own\nINFO task=task-a-other neighbor\nINFO system-wide",'count':3})
 monkeypatch.setattr(m.engine_log,'read',lambda *a:{'available':False,'text':'','count':0,'path':'test'})
 got=m.server_log(30,SimpleNamespace(id='operator',admin=True),'task-a')
 assert got['text']=='INFO task=task-a own' and got['count']==1
 assert got['task_id']=='task-a' and 'unattributed' in got['task_filter']
