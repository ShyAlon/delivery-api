"""Read-only projection of owned PostgreSQL V-model evidence."""
from __future__ import annotations
import json
from datetime import datetime,timezone

def _rows(value,*keys):
 if isinstance(value,list):return value
 if isinstance(value,dict):
  for k in keys:
   if isinstance(value.get(k),list):return value[k]
 return []
def _project_bundle(summary,interview_status,intent,conops,needs,srs,arch,items,tests,validation,integrity,run,audits,coverage):
 reqs=_rows(srs,"functional","requirements")+_rows(srs,"non_functional")
 nodes=[];edges=[]
 for n in _rows(needs,"needs"):nodes.append({"id":n.get("need_id"),"kind":"need","label":n.get("statement") or n.get("title"),"data":n})
 for r in reqs:
  rid=r.get("requirement_id");nodes.append({"id":rid,"kind":"requirement","label":r.get("title") or r.get("statement"),"data":r})
  for nid in r.get("need_ids") or []:edges.append({"from":nid,"to":rid,"kind":"realizes"})
  for ac in r.get("acceptance") or []:
   aid=ac.get("criterion_id");nodes.append({"id":aid,"kind":"criterion","label":ac.get("statement"),"data":ac});edges.append({"from":rid,"to":aid,"kind":"accepts"})
 for item in items:
  iid=item.get("spec_id") or item.get("item_id");nodes.append({"id":iid,"kind":"spec","label":item.get("title") or item.get("name"),"data":item})
  for rid in item.get("requirement_ids") or []:edges.append({"from":rid,"to":iid,"kind":"specified-by"})
 for t in tests:
  tid=t.get("test_id");nodes.append({"id":tid,"kind":"test","label":t.get("title") or t.get("name"),"data":t})
  for source in (t.get("requirement_ids") or [])+(t.get("criterion_ids") or [])+(t.get("spec_ids") or []):edges.append({"from":source,"to":tid,"kind":"verified-by"})
 nodes=[n for n in nodes if n["id"]]
 # Status is derived from this session's artifacts, run state and diagnostics - never a UI constant.
 diagnostic_rows=integrity.get("diagnostics",[]) if isinstance(integrity,dict) else []
 phase_values={"interview":intent or conops,"needs":_rows(needs,"needs"),"srs":reqs,"architecture":arch,"specs":items,"tests":tests,"validation":validation,"reviews":audits,"oracle":integrity}
 phase_status={}
 run_phases=run.get("phases",{}) if isinstance(run,dict) else {}
 for key,value in phase_values.items():
  explicit=run_phases.get(key) if isinstance(run_phases,dict) else None
  explicit=explicit.get("status") or explicit.get("state") if isinstance(explicit,dict) else explicit
  related=[d for d in diagnostic_rows if not isinstance(d,dict) or d.get("phase") in (None,key) or d.get("artifact","").startswith(key)]
  severity={str(d.get("severity",d.get("level","error"))).lower() for d in related if isinstance(d,dict)}
  if explicit in ("running","in_progress"):state="in_progress"
  elif explicit in ("error","failed") or "error" in severity:state="error"
  elif explicit in ("warning","warn") or severity & {"warning","warn"}:state="warning"
  elif value:state="done"
  else:state="not_started"
  phase_status[key]=state
 phase_status["interview"]=interview_status
 return {"session":summary,"phase_status":phase_status,"intent":intent,"conops":conops,"coverage":coverage,"needs":_rows(needs,"needs"),"srs":srs,"requirements":reqs,"architecture":arch,"items":items,"tests":tests,"validation":validation,"audits":audits,"integrity":integrity,"run":run,"graph":{"nodes":nodes,"edges":edges}}


def postgres_bundle(store,session):
 """Project the current owned execution's immutable publication and PG supplements."""
 binding=store.session_binding(session)
 if not binding:raise FileNotFoundError(session)
 task=store.task(binding['task_id'])
 if not task or task['engine_session_id']!=session:raise FileNotFoundError(session)
 snapshot=store.manifest_snapshot(task['task_id'],binding['generation'])
 documents={}
 for row in (snapshot or {}).get('rows',[]):
  if not row['stale']:documents[row['name']]=store.read_artifact_bytes_sha(row['sha256'])
 def load(name,default):
  raw=documents.get(name)
  return json.loads(raw) if raw is not None else default
 from .generator_documents import GeneratorDocuments
 reader=GeneratorDocuments.owner_reader(store,task['task_id'])
 def supplemental(key,default):
  raw=reader.read(key)
  return json.loads(raw) if raw is not None else default
 intent=load('intent_model.json',{});conops=load('conops.json',{})
 needs=load('needs.json',{});srs=load('srs.json',{});arch=load('architecture.json',{})
 items=_rows(load('items.json',[]),'items','specs');tests=_rows(load('tests.json',[]),'tests');validation=_rows(load('validation.json',{}),'items')
 integrity=supplemental('spec/bundle-integrity.json',{});run=supplemental('spec/hands-free-run.json',{})
 coverage=supplemental('interview/coverage.json',{});audits=[]
 for area in ('interview','spec'):
  raw=reader.read(area+'/audit.jsonl')
  if raw is not None:
   for line in raw.decode().splitlines():
    try:audits.append(json.loads(line))
    except json.JSONDecodeError:pass
 summary={'session_id':session,'title':intent.get('problem_statement') or task['title'],
  'completed_at':run.get('completed_at'),'updated_at':datetime.fromtimestamp(task['updated_at'],timezone.utc).isoformat(),
  'oracle_passed':bool(integrity) and not bool(integrity.get('diagnostics')),'files_checked':integrity.get('files_checked',0),
  'phases':[area for area,present in (('interview',intent or conops),('spec',needs or srs or arch or items or tests or validation)) if present]}
 http=supplemental('interview/http-session.json',{})
 status=http.get('status') if isinstance(http,dict) else None
 usage=reader.read('interview/usage.jsonl')
 interview_status=('done' if intent else 'error' if status in (50,60) else 'warning' if status==40
                   else 'in_progress' if conops or usage is not None or status in (10,20,30) else 'not_started')
 if not intent and isinstance(http,dict):
  brief=(http.get('request') or {}).get('idea')
  if isinstance(brief,str) and brief.strip():summary['title']=brief.strip()
 result=_project_bundle(summary,interview_status,intent,conops,needs,srs,arch,items,tests,validation,integrity,run,audits,coverage)
 summary['phase_status']=result['phase_status']
 return result
