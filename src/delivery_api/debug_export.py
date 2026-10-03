"""Bounded diagnostic export. No user-selected filesystem paths or uploads."""
from __future__ import annotations
import io,json,re,zipfile,os
from pathlib import Path
from datetime import datetime,timezone

SENSITIVE=re.compile(r'(password|passcode|secret|token|authorization|cookie|api[_-]?key|credential|private[_-]?key)',re.I)
SECRET_TEXT=re.compile(r'''(?ix)(\b(?:authorization|cookie|password|api[_-]?key|token|secret)\b["']?\s*[:=]\s*)[^\n,;]+''')
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

def diagnostic_zip(root:Path,task_id:str,session_id:str,generation:int,browser:dict,control:dict,events:list,log_path:Path)->bytes:
 files={};omissions=[];limit=2_000_000;total_limit=12_000_000
 def add(name,content,source):
  if isinstance(content,str):text=redact(content)
  else:text=json.dumps(redact(content),ensure_ascii=False,indent=2)
  raw=text.encode()
  if len(raw)>limit or sum(len(x) for x in files.values())+len(raw)>total_limit:
   omissions.append({'source':source,'reason':'export size limit'});return
  files[name]=raw
 def read(path,name):
  if not path.is_file():omissions.append({'source':name,'reason':'not recorded'});return
  # Reject symlinks escaping the canonical run. No arbitrary traversal.
  if not path.resolve().is_relative_to(root.resolve()):omissions.append({'source':name,'reason':'outside run root'});return
  if path.stat().st_size>limit:omissions.append({'source':name,'reason':'source over 2 MB limit'});return
  text=path.read_text(errors='replace')
  if path.suffix=='.jsonl':
   rows=[]
   for line in text.splitlines():
    try:rows.append(json.loads(line))
    except ValueError:rows.append({'unparsed':line})
   add(name,rows,name)
  else:add(name,text,name)
 for phase_dir in ('interview','spec'):
  for name in ('run.log','llm-calls.jsonl','debug-http.jsonl','usage.jsonl'):
   read(root/phase_dir/name,f'{phase_dir}/{name}')
 # Per-turn question verification scores (interview-engine E5): written by the
 # interview phase only, one JSON object per probing turn, for calibration.
 read(root/'interview'/'verification.jsonl','interview/verification.jsonl')
 # Server run.log is global. Filter by exact task/session to avoid another
 # task's project data; export a truthful bounded tail with its capture scope.
 if log_path.is_file():
  with log_path.open('rb') as stream:
   stream.seek(max(0,log_path.stat().st_size-limit));tail=stream.read(limit).decode(errors='replace')
  ids=[f"task_id={task_id!r}",f"task={task_id!r}",f"session_id={session_id!r}",f"session={session_id!r}"]
  lines=[line for line in tail.splitlines() if any(x in line for x in ids)]
  if lines:add('run.log','\n'.join(lines)+'\n','scoped server run log tail')
  else:omissions.append({'source':'run.log','reason':'no task/session-matched lines in bounded tail'})
 else:omissions.append({'source':'run.log','reason':'not recorded'})
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
