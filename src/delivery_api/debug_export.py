"""Bounded diagnostic export. No user-selected filesystem paths or uploads."""
from __future__ import annotations
import io,json,re,zipfile,os,shlex
from pathlib import Path
from datetime import datetime,timezone

SENSITIVE=re.compile(r'(password|passwd|passcode|secret|token|authorization|cookie|api[_-]?key|credential|private[_-]?key)',re.I)
SECRET_TEXT=re.compile(r'''(?ix)(["']?\b[A-Za-z0-9_.-]*(?:password|passwd|passcode|secret|token|authorization|cookie|api[_-]?key|credential|private[_-]?key)[A-Za-z0-9_.-]*\b["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
BEARER=re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+')

def redact(value):
 if isinstance(value,dict):return {k:('[REDACTED]' if SENSITIVE.search(k) else redact(v)) for k,v in value.items()}
 if isinstance(value,list):return [redact(v) for v in value]
 if isinstance(value,str):
  text=BEARER.sub('Bearer [REDACTED]',SECRET_TEXT.sub(r'\1[REDACTED]',value))
  for name,secret in os.environ.items():
   if SENSITIVE.search(name) and len(secret)>=8:text=text.replace(secret,'[REDACTED]')
  return text
 return value

def _owned_log_line(line: str, task_id: str, session_id: str) -> bool:
 # Parse complete fields, not substrings inside another id, field or quoted
 # message. Unattributed/continuation/malformed records fail closed.
 if not line or len(line)>16_000 or line[0].isspace():return False
 try:tokens=shlex.split(line,comments=False,posix=True)
 except ValueError:return False
 expected={"task":task_id,"task_id":task_id,"session":session_id,"session_id":session_id}
 matched=False
 for token in tokens:
  key,sep,value=token.partition('=')
  if sep and key in expected:
   if value!=expected[key]:return False
   matched=True
 return matched


_FIELD=__import__('re').compile(r"(\w+)='([^']*)'")
_BUDGET_FIELDS=('ref','method','path','status','duration_ms','db_queries','db_ms','checkout_ms','auth_ms','held_ms','engine_calls','engine_ms')

def request_budgets(lines):
 """One compact row per delivery request: the per-request stats the activity
 record already carries (db_queries/db_ms/checkout_ms/...), so redundant-query
 cost is visible in the export without reading raw log lines."""
 budgets=[]
 for line in lines or []:
  if '| http.request' not in line:continue
  fields=dict(_FIELD.findall(line))
  row={'ts':line.split(' | ',1)[0].strip()}
  for key in _BUDGET_FIELDS:
   value=fields.get(key)
   if value is None:continue
   row[key]=int(value) if value.isdigit() else value
  budgets.append(row)
 return budgets

def diagnostic_zip(root:Path,task_id:str,session_id:str,generation:int,browser:dict,control:dict,events:list,log_path:Path,engine_log_path:Path=None,*,document_reader)->bytes:
 files={};omissions=[];limit=2_000_000;total_limit=12_000_000
 def add(name,content,source):
  if isinstance(content,str):text=redact(content)
  else:text=json.dumps(redact(content),ensure_ascii=False,indent=2)
  raw=text.encode()
  if len(raw)>limit or sum(len(x) for x in files.values())+len(raw)>total_limit:
   omissions.append({'source':source,'reason':'export size limit'});return
  files[name]=raw
 def read(path,name):
  raw=document_reader(name)
  if raw is None:omissions.append({'source':name,'reason':'not recorded in owned PG documents'});return
  if len(raw)>limit:omissions.append({'source':name,'reason':'source over 2 MB limit'});return
  text=raw.decode(errors='replace')
  if path.suffix=='.jsonl':
   rows=[]
   for line in text.splitlines():
    try:rows.append(json.loads(line))
    except ValueError:rows.append({'unparsed':line})
   add(name,rows,'owned PG document')
  else:add(name,text,'owned PG document')
 for phase_dir in ('interview','spec'):
  for name in ('run.log','llm-calls.jsonl','debug-http.jsonl','usage.jsonl'):
   read(root/phase_dir/name,f'{phase_dir}/{name}')
 # Per-turn question verification scores (interview-engine E5): written by the
 # interview phase only, one JSON object per probing turn, for calibration.
 read(root/'interview'/'verification.jsonl','interview/verification.jsonl')
 # Server run.log and the engine process log are global. Filter each by exact
 # task/session to avoid another task's project data; export a truthful
 # bounded tail with its capture scope. Delivery activity lines quote the ids
 # (task_id='x'); engine dotted-event lines do not (session=x) - both forms
 # are matched so the engine's markers reach the per-task ZIP.
 def add_log_tail(path,name,scope):
  if not path or not path.is_file():omissions.append({'source':name,'reason':'not recorded'});return []
  with path.open('rb') as stream:
   offset=max(0,path.stat().st_size-limit)
   stream.seek(offset);tail=stream.read(limit).decode(errors='replace')
  # A bounded tail may start inside another record. Never promote that
  # fragment into a complete attributed line.
  if offset:tail=tail.partition('\n')[2]
  lines=[line for line in tail.splitlines() if _owned_log_line(line,task_id,session_id)]
  if lines:add(name,'\n'.join(lines)+'\n',scope)
  else:omissions.append({'source':name,'reason':'no task/session-matched lines in bounded tail'})
  return lines
 run_log_lines=add_log_tail(log_path,'run.log','scoped server run log tail')
 budgets=request_budgets(run_log_lines)
 if budgets:add('request-budgets.json',{'scope':'delivery requests matched to this task in the bounded run log tail; one row per request, values as recorded at request end','requests':budgets},'derived from scoped server run log tail')
 if engine_log_path is not None:add_log_tail(engine_log_path,'engine.log','scoped engine process log tail')
 add('task-events.json',events,'task event snapshot')
 add('control.json',control,'current control snapshot')
 add('browser.json',browser,'this browser captured diagnostics')
 manifest={'export_version':1,'task_id':task_id,'session_id':session_id,'generation':generation,
  'created_at':datetime.now(timezone.utc).isoformat(),'included':list(files),'omissions':omissions,
  'browser_capture':{'started_at':browser.get('started_at'),'ended_at':browser.get('ended_at'),'scope':'current browser tab since capture started; not historical browser recovery'},
  'limits':{'per_source_bytes':limit,'total_bytes':total_limit,'server_log':'last 2 MB filtered to exact task/session'},
  'privacy':'Credentials are redacted. Project content can remain; downloading does not share or upload this ZIP.'}
 add('manifest.json',manifest,'manifest')
 data=io.BytesIO()
 with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
  for name,value in files.items():z.writestr(name,value)
 return data.getvalue()
