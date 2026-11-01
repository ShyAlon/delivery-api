"""Ephemeral bounded server process logs. Audit/accounting are separate DB stores."""
import os,json,time,math,fcntl
from pathlib import Path
STREAMS=frozenset(('activity','diagnostics','engine',*(
    'stage:'+p for p in ('intent','conops','needs','srs','architecture','specs','tests','validation'))))

def retention(stream):
    if stream not in STREAMS:raise ValueError('fixed operator log stream required')
    from .log_budget import BUDGET
    size,count=BUDGET['stages' if stream.startswith('stage:') else stream]
    return size*count//(8 if stream.startswith('stage:') else 1)

def root():
    from .config import default_data_dir
    return default_data_dir()/'logs'/'process'

class FileLogs:
    def append(self,stream,text,*,task_id=None,session_id=None,ts=None,**unused):
        if stream not in STREAMS:raise ValueError('fixed stream required')
        from .server_log_buffer import scrub
        for value in (task_id,session_id):
            if value is not None and (not isinstance(value,str) or len(value)>240 or any(ord(c)<32 for c in value)):
                raise ValueError('bounded log identity required')
        stamp=time.time() if ts is None else ts
        if not isinstance(stamp,(int,float)) or not math.isfinite(stamp):raise ValueError('finite timestamp required')
        # Preserve parseable diagnostics while scrubbing values, not JSON syntax.
        if stream=='diagnostics':
            body=json.loads(text)
            if not isinstance(body,dict) or set(body)-{'at','level','logger','message','request_id','task_id','session_id','job_id','error_class','frames','causes'}:
                raise ValueError('fixed diagnostic projection required')
            # Scrub values, never the serialized JSON syntax. Truncating the
            # JSON envelope makes restart-readable diagnostic evidence invalid.
            projected={}
            for key,value in body.items():
                if key=='causes':
                    if not isinstance(value,list) or len(value)>4:raise ValueError('bounded causes required')
                    from .child_failure import parse,PREFIX
                    causes=[]
                    for cause in value:
                        validated=parse(PREFIX+json.dumps(cause).encode())
                        if validated is None:raise ValueError('shape-only cause required')
                        causes.append(validated)
                    projected[key]=causes
                    continue
                if key!='frames':
                    projected[key]=scrub(str(value))
                    continue
                if not isinstance(value,list) or len(value)>30:
                    raise ValueError('bounded diagnostic frames required')
                frames=[]
                for frame in value:
                    if not isinstance(frame,dict) or set(frame)-{'file','function','line'}:
                        raise ValueError('fixed diagnostic frame projection required')
                    item={}
                    for field,entry in frame.items():
                        if field=='line':
                            if type(entry) is not int or not 0<=entry<=100000000:
                                raise ValueError('integer diagnostic line required')
                            item[field]=entry
                        elif isinstance(entry,str):item[field]=scrub(entry)
                        else:raise ValueError('text diagnostic frame required')
                    frames.append(item)
                projected[key]=frames
            body=projected
            text=json.dumps(body,ensure_ascii=False)
        else:text=scrub(str(text))
        row={'stream':stream,'ts':stamp,'text':text,'task_id':task_id,'session_id':session_id}
        raw=(json.dumps(row,ensure_ascii=False)+'\n').encode()
        if len(raw)>65536:raise ValueError('bounded file record required')
        base=root();base.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=base/(stream.replace(':','-')+'.jsonl')
        # Supervisor and delivery can share engine stream. Lock is held only on
        # the writer thread, never on a request thread or asyncio loop.
        with open(str(path)+'.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            cap=retention(stream)//2
            if path.exists() and path.stat().st_size+len(raw)>cap:
                os.replace(path,str(path)+'.1')
            with os.fdopen(os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600),'ab') as out:out.write(raw)

    def records(self,*,stream=None,task_id=None,session_id=None,limit=2000):
        rows=[];unreadable=[]
        for key in ([stream] if stream else sorted(STREAMS)):
            if key not in STREAMS:raise ValueError('fixed stream required')
            path=root()/(key.replace(':','-')+'.jsonl')
            for source in (Path(str(path)+'.1'),path):
                try:
                    with source.open('rb') as f:
                        f.seek(0,2);start=max(0,f.tell()-retention(key)//2);f.seek(start)
                        if start:f.readline()
                        for line in f:
                            try:row=json.loads(line)
                            except ValueError:continue
                            if task_id is None or row.get('task_id')==task_id or (session_id and row.get('session_id')==session_id):rows.append(row)
                except FileNotFoundError:pass
                except OSError as error:unreadable.append(type(error).__name__)
        rows.sort(key=lambda r:r['ts'])
        return {'records':rows[-limit:],'truncated':len(rows)>limit,'unreadable':unreadable}

    def tail(self,stream,*,limit=400):return self.records(stream=stream,limit=limit)['records']

def selected():return FileLogs()

import threading
_emitting=threading.local()


# One bounded process writer, using bounded rotating files. Logging handler
# locks protect enqueue only, never file writes. No database connection.
import queue
import time as _clock
_pending=queue.Queue(maxsize=1024)
_writer_lock=threading.Lock()
_writer=None
_active_since=None

def _write_loop():
    while True:
        repo,stream,text,fields,queued_at=_pending.get()
        global _active_since
        _active_since=queued_at
        _emitting.active=True
        try:repo.append(stream,text,**fields)
        except Exception as error:
            from .log_durability import degraded
            degraded('process-files',error)
        finally:
            _emitting.active=False
            _active_since=None
            _pending.task_done()

def writer_status():
    with _pending.mutex:
        oldest=_active_since or (_pending.queue[0][4] if _pending.queue else None)
        return {'pending':_pending.unfinished_tasks,'capacity':_pending.maxsize,
                'oldest_pending_ms':int((_clock.monotonic()-oldest)*1000) if oldest is not None else 0,
                'acknowledgement':'accepted to bounded memory queue, not durable file acknowledgement'}

def flush(timeout=5):
    """Bounded shutdown/test drain. Enqueue is not durable success."""
    deadline=_clock.monotonic()+timeout
    while _pending.unfinished_tasks and _clock.monotonic()<deadline:_clock.sleep(.005)
    if _pending.unfinished_tasks:
        from .log_durability import degraded
        degraded('process-files-drain',TimeoutError())
        return False
    return True

def emit(stream,text,**fields):
    """True selects ephemeral files, not a persisted acknowledgement.

    Queue overflow/failure is explicit degraded evidence, never silent success.
    """
    global _writer
    if getattr(_emitting,'active',False):return True
    try:
        repo=selected()
        # Bound queued data before admission. append performs final projection.
        text=str(text)
        if len(text.encode('utf-8'))>32768:
            if stream=='diagnostics':raise ValueError('bounded diagnostic queue record required')
            text=text.encode('utf-8')[:32700].decode('utf-8','ignore')+' [truncated]'
        fields=dict(fields)
        fields.setdefault('ts',time.time())
        _pending.put_nowait((repo,stream,text,fields,_clock.monotonic()))
        with _writer_lock:
            if _writer is None or not _writer.is_alive():
                _writer=threading.Thread(target=_write_loop,name='file-log-writer',daemon=True);_writer.start()
    except Exception as error:
        from .log_durability import degraded
        degraded('process-files',error)
    return True


def operator_view(reader,owner_store,minutes,*,task_id=None,download=False,now=None):
    """Registry-gated bounded cross-host view. Downloads omit frame/source text."""
    import json
    from datetime import datetime,timezone
    from .server_log_buffer import scrub,EXPORT_MESSAGE_CAP
    now=time.time() if now is None else now
    minutes=max(1,min(int(minutes),240));since=now-minutes*60
    with reader._read(owner_store,'logs.read') as (_,authority):pass
    result=selected().records(task_id=task_id,limit=2000)
    result['registry_version']=authority['registry_version']
    lines=[];engine_lines=0
    for row in result['records']:
        if row['ts']<since:continue
        stream=row['stream'];text=row['text']
        # The old ZIP never included engine stdout. Keep that safety boundary:
        # arbitrary stdout cannot distinguish source code from message text.
        if download and stream=='engine':continue
        if stream=='diagnostics':
            try:
                body=json.loads(text)
                text='%s %s: %s'%(body.get('level',''),body.get('logger',''),body.get('message',''))
            except (ValueError,TypeError):continue
        text=scrub(text)
        if download and stream!='activity' and len(text)>EXPORT_MESSAGE_CAP:
            text=text[:EXPORT_MESSAGE_CAP]+' [cut]'
        stamp=datetime.fromtimestamp(row['ts'],timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        lines.append('%s %s: %s'%(stamp,stream,text))
        if stream=='engine':engine_lines+=1
    return {'text':'\n'.join(lines),'count':len(lines),'minutes':minutes,'truncated':result['truncated'],
        'scope':'reviewed_server_file_operator_read','registry_version':result['registry_version'],
        'buffer_full':result['truncated'],'scrubbed':True,
        'since':datetime.fromtimestamp(since,timezone.utc).isoformat(),'until':datetime.fromtimestamp(now,timezone.utc).isoformat(),
        'engine_log':{'available':True,'availability':'available','lines':engine_lines,'path':'ephemeral:server-files/engine'},
        'unreadable':result['unreadable'],
        'availability':'unreadable' if result['unreadable'] else 'available',
        'storage':'Ephemeral bounded server files; may be lost on restart'}


import atexit
atexit.register(flush,5)


def exception_blocks(rows):
    """Deterministic shape extraction, never raw traceback values/source lines."""
    import re
    blocks=[];current=None
    def finish():
        nonlocal current
        if current:blocks.append(current);current=None
    for row in rows:
        text=row['text']
        if row['stream']=='diagnostics':
            try:body=json.loads(text)
            except ValueError:continue
            if body.get('error_class'):
                finish();blocks.append({'ts':row['ts'],'source':'structured',
                    'error_class':body['error_class'],'frames':body.get('frames',[]),
                    'causes':body.get('causes',[])})
            continue
        for line in text.splitlines():
            if 'Traceback (most recent call last):' in line:
                finish();current={'ts':row['ts'],'source':'retained traceback scan','frames':[]}
            elif current is not None:
                frame=re.search(r'File "([^"\n]+)", line ([0-9]+), in ([A-Za-z0-9_.<>-]+)',line)
                if frame:
                    current['frames'].append({'file':Path(frame[1]).name,'line':int(frame[2]),'function':frame[3]})
                else:
                    error=re.search(r'(?:^|\s)([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Exit|Interrupt))(?::|$)',line)
                    if error:current['error_class']=error[1];finish()
    finish()
    return {'blocks':blocks,'found':len(blocks),'scan':'deterministic structured exceptions and retained traceback markers',
        'values_excluded':True,'scope':'only retained exact task/session rows; no recovery of rotated/never-recorded blocks',
        'truncation':'inherits log-window retention/export limits; trailing partial blocks may lack class or frames'}


def task_window(reader,owner_store,task_id,session_id,since,*,until=None):
    """All retained attributed rows, not a 400-line tail; explicit export limits."""
    from .server_log_buffer import scrub
    with reader._read(owner_store,'logs.read'):pass
    until=time.time() if until is None else until
    result=selected().records(task_id=task_id,session_id=session_id,limit=100000)
    rows=[];size=0;cut=False
    for row in result['records']:
        if not since<=row['ts']<=until:continue
        # Raw traceback source/value lines must not enter an export. Keep their
        # safe shape in exception_blocks, just like structured parent failures.
        row=dict(row)
        if row['stream']=='engine':
            lines=row['text'].splitlines()
            safe=[]
            for line in lines:
                if (line[:1].isspace() or 'Traceback (most recent call last):' in line
                    or __import__('re').search(r'File "[^"\n]+", line [0-9]+|[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Exit|Interrupt):',line)):
                    continue
                safe.append(scrub(line))
            row['text']='\n'.join(safe)
        raw=json.dumps(row,ensure_ascii=False)
        if size+len(raw.encode())>12000000:cut=True;break
        rows.append(row);size+=len(raw.encode())
    return {'records':rows,'exception_blocks':exception_blocks([r for r in result['records'] if since<=r['ts']<=until]),'since':since,'until':until,'unreadable':result['unreadable'],
        'truncated':result['truncated'] or cut,'limits':{'rows':100000,'bytes':12000000},
        'scope':'retained exact task/session-attributed delivery, diagnostic and engine records',
        'omissions':['Unattributed lines excluded for privacy.','Files are rotating ephemeral storage; earlier rotated/restarted or never-recorded output is unrecoverable.',
            'Raw provider audit/payloads and host Ollama/OS logs excluded.'],'storage':'ephemeral retained files'}
