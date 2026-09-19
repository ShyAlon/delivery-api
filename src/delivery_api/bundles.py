"""Read-only discovery and projection of completed on-disk V-model bundles."""
from __future__ import annotations
import json,os,re
from pathlib import Path
from . import config

SETTINGS_FILE=Path(__file__).resolve().parents[2]/".artifacts-root"
def default_root()->Path:
 return Path(__file__).resolve().parents[3]/"artifacts"
def root()->Path:
 configured=os.environ.get("VMODEL_ARTIFACTS_DIR") or os.environ.get("DELIVERY_ARTIFACT_DIR")
 if configured:return Path(configured).expanduser().resolve()
 try:
  saved=SETTINGS_FILE.read_text().strip()
  if saved:return Path(saved).expanduser().resolve()
 except OSError:pass
 return default_root()
def inspect_root(candidate:str)->dict:
 path=Path(candidate).expanduser().resolve()
 if not path.is_dir():raise ValueError(f"Artifacts folder does not exist: {path}")
 sessions=_discover(path)
 if not sessions:raise ValueError(f"No loadable sessions found in {path}; expected session/interview or session/spec folders")
 return {"path":str(path),"sessions":sessions,"count":len(sessions)}
def save_root(candidate:str)->dict:
 result=inspect_root(candidate);SETTINGS_FILE.write_text(result["path"]+"\n");return result
def _json(path:Path,default=None):
 try:return json.loads(path.read_text())
 except (OSError,json.JSONDecodeError):return default
def _discover(where:Path)->list[dict]:
 out=[]
 if not where.is_dir():return out
 for d in where.iterdir():
  if not d.is_dir():continue
  interview=d/"interview";spec=d/"spec"
  has_interview=interview.is_dir() and any((interview/n).is_file() for n in ("intent_model.json","conops.json","usage.jsonl"))
  has_spec=spec.is_dir() and any((spec/n).is_file() for n in ("needs.json","srs.json","architecture.json","bundle-integrity.json","usage.jsonl"))
  if not has_interview and not has_spec:continue
  run=_json(spec/"hands-free-run.json",{}) or {};integrity=_json(spec/"bundle-integrity.json",{}) or {};intent=_json(interview/"intent_model.json",{}) or {}
  phases=[x for x,present in (("interview",has_interview),("spec",has_spec)) if present]
  diagnostics=integrity.get("diagnostics") if isinstance(integrity,dict) else None
  out.append({"session_id":d.name,"title":intent.get("problem_statement") or intent.get("core_value_proposition") or d.name,
   "completed_at":run.get("completed_at"),"oracle_passed":bool(integrity) and not bool(diagnostics),"files_checked":integrity.get("files_checked",0),"phases":phases,"phase_status":_phase_status(interview,spec,run,integrity),"path":str(d)})
 return sorted(out,key=lambda x:(x["completed_at"] or "",x["session_id"]),reverse=True)
def _phase_status(interview:Path,spec:Path,run:dict,integrity:dict)->dict:
 values={"interview":(interview/"intent_model.json").is_file(),"needs":(spec/"needs.json").is_file(),"srs":(spec/"srs.json").is_file(),"architecture":(spec/"architecture.json").is_file(),"specs":(spec/"items.json").is_file() or (spec/"spec_items.json").is_file(),"tests":(spec/"tests.json").is_file() or (spec/"test_specifications.json").is_file(),"reviews":(interview/"audit.jsonl").is_file() or (spec/"audit.jsonl").is_file(),"oracle":bool(integrity)}
 explicit=run.get("phases",{}) if isinstance(run,dict) else {};diagnostics=integrity.get("diagnostics",[]) if isinstance(integrity,dict) else []
 out={}
 for key,present in values.items():
  state=explicit.get(key) if isinstance(explicit,dict) else None;state=state.get("status") or state.get("state") if isinstance(state,dict) else state
  related=[x for x in diagnostics if isinstance(x,dict) and x.get("phase") in (None,key)]
  levels={str(x.get("severity",x.get("level","error"))).lower() for x in related}
  out[key]="in_progress" if state in ("running","in_progress") else "error" if state in ("error","failed") or "error" in levels else "warning" if state in ("warning","warn") or levels & {"warning","warn"} else "done" if present else "not_started"
 return out

def discover()->list[dict]:return _discover(root())
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
 coverage=_json(interview/"coverage.json",{}) or {}
 # Status is derived from this session's artifacts, run state and diagnostics - never a UI constant.
 diagnostic_rows=integrity.get("diagnostics",[]) if isinstance(integrity,dict) else []
 phase_values={"interview":intent or conops,"needs":_rows(needs,"needs"),"srs":reqs,"architecture":arch,"specs":items,"tests":tests,"reviews":audits,"oracle":integrity}
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
 return {"session":next((x for x in discover() if x["session_id"]==session),{"session_id":session}),"phase_status":phase_status,"intent":intent,"conops":conops,"coverage":coverage,"needs":_rows(needs,"needs"),"srs":srs,"requirements":reqs,"architecture":arch,"items":items,"tests":tests,"audits":audits,"integrity":integrity,"run":run,"graph":{"nodes":nodes,"edges":edges}}
