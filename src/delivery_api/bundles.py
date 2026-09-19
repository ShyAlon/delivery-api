"""Read-only discovery and projection of completed on-disk V-model bundles."""
from __future__ import annotations
import json,os,re
from pathlib import Path
from . import config

def root()->Path:
 configured=os.environ.get("VMODEL_ARTIFACTS_DIR") or os.environ.get("DELIVERY_ARTIFACT_DIR")
 return Path(configured).expanduser().resolve() if configured else (Path(__file__).resolve().parents[3]/"artifacts")
def _json(path:Path,default=None):
 try:return json.loads(path.read_text())
 except (OSError,json.JSONDecodeError):return default
def discover()->list[dict]:
 out=[]
 if not root().is_dir():return out
 for d in root().iterdir():
  if not d.is_dir() or not (d/"spec").is_dir():continue
  spec=d/"spec";run=_json(spec/"hands-free-run.json",{}) or {};integrity=_json(spec/"bundle-integrity.json",{}) or {};intent=_json(d/"interview/intent_model.json",{}) or {}
  if not run and not integrity:continue
  out.append({"session_id":d.name,"title":intent.get("problem_statement") or intent.get("core_value_proposition") or d.name,
   "completed_at":run.get("completed_at"),"oracle_passed":not bool(integrity.get("diagnostics")),"files_checked":integrity.get("files_checked",0),"path":str(d)})
 return sorted(out,key=lambda x:(x["completed_at"] or "",x["session_id"]),reverse=True)
def _first(spec:Path,*names,default=None):
 for n in names:
  if (spec/n).is_file():return _json(spec/n,default)
 return default
def _rows(value,*keys):
 if isinstance(value,list):return value
 if isinstance(value,dict):
  for k in keys:
   if isinstance(value.get(k),list):return value[k]
 return []
def bundle(session:str)->dict:
 if not re.fullmatch(r"[A-Za-z0-9._-]+",session):raise ValueError("invalid session")
 base=(root()/session).resolve()
 if base.parent!=root() or not base.is_dir():raise FileNotFoundError(session)
 spec=base/"spec";interview=base/"interview"
 intent=_json(interview/"intent_model.json",{}) or {};conops=_json(interview/"conops.json",{}) or {}
 needs=_first(spec,"needs.json",default={}) or {};srs=_first(spec,"srs.json",default={}) or {};arch=_first(spec,"architecture.json",default={}) or {}
 items=_rows(_first(spec,"items.json","spec_items.json",default=[]),"items","specs")
 tests=_rows(_first(spec,"tests.json","test_specifications.json",default=[]),"tests")
 integrity=_json(spec/"bundle-integrity.json",{}) or {};run=_json(spec/"hands-free-run.json",{}) or {}
 audits=[]
 for area in (interview,spec):
  p=area/"audit.jsonl"
  if p.is_file():
   for line in p.read_text().splitlines():
    try: audits.append(json.loads(line))
    except json.JSONDecodeError:pass
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
 return {"session":next((x for x in discover() if x["session_id"]==session),{"session_id":session}),"intent":intent,"conops":conops,"needs":_rows(needs,"needs"),"srs":srs,"requirements":reqs,"architecture":arch,"items":items,"tests":tests,"audits":audits,"integrity":integrity,"run":run,"graph":{"nodes":nodes,"edges":edges}}
