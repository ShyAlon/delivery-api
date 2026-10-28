"""Operator-only restart-readable diagnostics, no database or raw payloads.

8 MiB for this stream within32 MiB managed global logs. Per-run provider
audit artifacts are separate and are never customer diagnostic exports.
"""
import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
from .config import default_data_dir
from .log_safety import safe_text
from .log_durability import degraded

context=contextvars.ContextVar('diagnostic_context',default={})
PATH=default_data_dir()/'logs'/'diagnostics.jsonl'
from .log_budget import BUDGET
MAX_BYTES,FILES=BUDGET['diagnostics']
BACKUPS=FILES-1
_installed=None


class EvidenceHandler(RotatingFileHandler):
 def __init__(self,path=PATH,*,max_bytes=MAX_BYTES,backups=BACKUPS):
  Path(path).parent.mkdir(parents=True,exist_ok=True)
  super().__init__(path,maxBytes=max_bytes,backupCount=backups,encoding='utf-8',delay=True)
  self.setLevel(logging.WARNING)
 def emit(self,record):
  if record.levelno<logging.WARNING:return
  try:
   fields=context.get()
   row={'at':datetime.fromtimestamp(record.created,timezone.utc).isoformat(),
        'level':record.levelname,'logger':safe_text(record.name,120),
        'message':safe_text(record.getMessage(),2000),
        **{k:safe_text(v,240) for k,v in fields.items() if k in ('request_id','task_id','session_id','job_id')}}
   if record.exc_info and record.exc_info[0]:
    row['error_class']=record.exc_info[0].__name__
    # Exception values/source text may contain SQL parameters or project text.
    import traceback
    row['frames']=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                   for f in traceback.extract_tb(record.exc_info[2])[-30:]]
   projected=logging.makeLogRecord({'msg':json.dumps(row,ensure_ascii=False),'levelno':record.levelno})
   super().emit(projected)
  except Exception as error:degraded('diagnostic-file',error)
 def handleError(self,record):
  import sys
  degraded('diagnostic-file',sys.exc_info()[1] or OSError())


def install():
 global _installed
 if _installed:return
 try:
  h=EvidenceHandler();logging.getLogger().addHandler(h)
  # Activity does not propagate to root. Install once on that branch too.
  logging.getLogger('delivery_api.activity').addHandler(h)
  logging.captureWarnings(True)
  _installed=h
 except OSError as error:degraded('diagnostic-file',error)


def record(code,**fields):
 # Structured call-site evidence only. Never pass raw SQL, params or driver text.
 logging.getLogger('delivery_api.diagnostic').warning('%s %s',safe_text(code,80),json.dumps({k:safe_text(v,300) for k,v in fields.items()}))


def query_failure(error):
 state=getattr(error,'sqlstate',None)
 causes={'23505':'unique constraint violation','23503':'foreign key constraint violation',
         '42501':'insufficient privilege','40001':'serialization failure','40P01':'deadlock detected',
         '57014':'query canceled','08006':'connection failure','08001':'connection could not be established'}
 record('db.query.failed',error_class=type(error).__name__,sqlstate=state,
        cause=causes.get(state,'database operation failed; raw driver detail excluded'))


def read(path=PATH,*,task_id=None,max_lines=500):
 max_lines=max(1,min(int(max_lines),2000));path=Path(path);rows=[];unreadable=[];truncated=False;available=False
 for source in reversed([path]+[path.with_name(path.name+'.'+str(n)) for n in range(1,BACKUPS+1)]):
  if not source.exists():continue
  available=True
  try:
   with source.open('rb') as f:
    f.seek(0,2);offset=max(0,f.tell()-MAX_BYTES);f.seek(offset);content=f.read(MAX_BYTES)
   if offset:content=content.partition(b'\n')[2];truncated=True
   for line in content.splitlines():
    try:row=json.loads(line)
    except (ValueError,UnicodeDecodeError):truncated=True;continue
    if task_id and row.get('task_id')!=task_id:continue
    rows.append(row)
    if len(rows)>max_lines:rows.pop(0);truncated=True
  except OSError as error:unreadable.append(type(error).__name__)
 return {'available':available,'text':'\n'.join(json.dumps(r,ensure_ascii=False) for r in rows),
         'count':len(rows),'truncated':truncated,'unreadable':unreadable,'task_id':task_id,
         'scope':'operator diagnostic evidence','storage':'rotating files, up to 8 MiB diagnostics within 32 MiB managed global logs; oldest overwritten'}


@contextmanager
def bound(**fields):
 token=context.set({**context.get(),**{k:v for k,v in fields.items() if v is not None}})
 try:yield
 finally:context.reset(token)


def failure(code):
 logging.getLogger('delivery_api.diagnostic').exception(code)
