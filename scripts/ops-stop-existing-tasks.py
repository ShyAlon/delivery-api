#!/usr/bin/env python3
"""Owner-scoped stop-only batch. Dry review first, never Delete or rewrite jobs."""
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def plan(store):
    rows=[]
    for task in sorted(store.list_tasks(),key=lambda t:t['task_id']):
        work=store.task_worker_state(task['task_id'],limit=2000)
        # Same blocked population as reset, including failed tasks with live jobs.
        blocked=(task['state'] in ('running','waiting_gate','stop_requested') or
            any(j['state'] in ('pending','paused','running') for j in work['worker_jobs']) or
            any(a['state'] in ('reserved','dispatched','uncertain') for a in work['provider_attempts']) or
            (bool(work['provider_attempts']) and task['state']!='stopped'))
        if not blocked:continue
        rows.append({'task_id':task['task_id'],'title':task['title'],'state':task['state'],
            'generation':task['generation'],'session':store.engine_session(task['task_id']),
            'work':work,'inventory_capped':len(work['worker_jobs'])>=2000 or len(work['provider_attempts'])>=2000})
    return {'tasks':rows,'fingerprint':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}


def apply(store,engine,reviewed,*,confirm=input):
    count=len(reviewed['tasks'])
    if confirm('Type STOP '+str(count)+' TASKS to request Stop only: ')!='STOP '+str(count)+' TASKS':
        raise ValueError('typed confirmation did not match; no Stop requested')
    if plan(store)!=reviewed:raise ValueError('reviewed plan changed; rerun dry review; no Stop requested')
    from delivery_api.cooperative_stop import request_stop
    results=[]
    for item in reviewed['tasks']:
        tid=item['task_id']
        if item['inventory_capped']:
            results.append({'task_id':tid,'status':'blocked','reason':'inventory cap; cannot prove scope'});continue
        try:
            # Never synthesize an engine response, release holds, or update job state.
            out=request_stop(store,engine,tid,reconcile=item['state']=='stop_requested')
            results.append({'task_id':tid,'status':'stopped' if out['delete_allowed'] else 'pending',
                'state':out['state'],'pending_reason':out['pending_reason']})
        except Exception as exc:
            # Error class only: transport errors can contain credentials/addresses.
            results.append({'task_id':tid,'status':'blocked','reason':type(exc).__name__,
                'next':'Inspect task Stop diagnostics; durable Stop may already be recorded. No automatic retry or Delete.'})
    return results


class PreviewEngine:
    """Use the preview's private token inside its container, never copy it out."""
    def __init__(self,container,owner,session,dsn):
        self.container=container;self.owner=owner;self.session=session
        code="import os,hashlib;print(hashlib.sha256(os.environ['DELIVERY_DATABASE_URL'].encode()).hexdigest())"
        out=subprocess.run(['docker','exec',container,'python','-c',code],capture_output=True,text=True,timeout=10)
        if out.returncode or out.stdout.strip()!=hashlib.sha256(dsn.encode()).hexdigest():
            raise ValueError('preview unavailable or selected database differs; no Stop requested')
    def _call(self,session_id,reconcile):
        code='''import os,json,sys,urllib.request
from uuid import UUID
sid=str(UUID(sys.argv[1]));owner=str(UUID(sys.argv[2]));auth=str(UUID(sys.argv[3]))
path='/v1/sessions/'+sid+'/stop'+('/reconcile' if sys.argv[4]=='1' else '')
request=urllib.request.Request('http://127.0.0.1:8010'+path,data=b'',headers={'X-Interview-Engine-Token':os.environ['INTERVIEW_ENGINE_TOKEN'],'X-Requi-Owner':owner,'X-Requi-Session-Id':auth},method='POST')
try:
 with urllib.request.urlopen(request,timeout=15) as response:print(response.read().decode())
except Exception:sys.exit(2)
'''
        out=subprocess.run(['docker','exec',self.container,'python','-c',code,session_id,self.owner,self.session,'1' if reconcile else '0'],capture_output=True,text=True,timeout=20)
        if out.returncode:raise RuntimeError('preview engine Stop unverified')
        from vmodel_contracts.interactive import EngineSnapshot
        return EngineSnapshot.model_validate_json(out.stdout)
    def stop(self,session_id):return self._call(session_id,False)
    def reconcile_stop(self,session_id):return self._call(session_id,True)
