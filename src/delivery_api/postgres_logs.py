"""Bounded process logs, isolated source writer and registry-gated operator reads.

No DDL, file fallback or tenant authority. Never log an error from this module
back through logging: callers use the nonrecursive degradation status instead.
"""
from contextlib import contextmanager
import math,time

STREAMS=frozenset(('activity','diagnostics','engine',*(
    'stage:'+p for p in ('intent','conops','needs','srs','architecture','specs','tests','validation'))))

def retention(stream):
    if stream not in STREAMS:raise ValueError('fixed operator log stream required')
    from .log_budget import BUDGET
    size,count=BUDGET['stages' if stream.startswith('stage:') else stream]
    return size*count//(8 if stream.startswith('stage:') else 1)


class PostgresLogs:
    def __init__(self,dsn,*,source_id):
        if not isinstance(dsn,str) or not dsn.startswith(('postgres://','postgresql://','host=')):
            raise ValueError('explicit PostgreSQL DSN required')
        if not isinstance(source_id,str) or not 1<=len(source_id)<=200 or any(ord(c)<32 for c in source_id):
            raise ValueError('stable bounded process source identity required')
        self.dsn,self.source_id=dsn,source_id

    @contextmanager
    def transaction(self,*,checkout_timeout=0.25):
        from .db_pool import pool_for
        with pool_for(self.dsn).connection(timeout=checkout_timeout) as c,c.transaction():
            c.execute('SET LOCAL ROLE requi_log_writer')
            c.execute("SET LOCAL statement_timeout='500ms'")
            c.execute("SET LOCAL lock_timeout='100ms'")
            c.execute("SELECT set_config('requi.log_source',%s,true)",(self.source_id,))
            yield c

    def save_startup(self,body):
        import json
        from psycopg.types.json import Jsonb
        from .server_log_buffer import scrub
        if not isinstance(body,dict) or set(body)-{'generated_at','status','checks'}:
            raise ValueError('fixed startup report projection required')
        def safe(value):
            if value is None or type(value) in (bool,int):return value
            if isinstance(value,str):return scrub(value)
            if isinstance(value,list):return [safe(v) for v in value]
            if isinstance(value,dict):return {k:safe(v) for k,v in value.items()}
            raise ValueError('safe startup projection required')
        body=safe(body)
        if len(json.dumps(body,ensure_ascii=False).encode('utf8'))>32768:
            raise ValueError('bounded startup report required')
        with self.transaction(checkout_timeout=10) as c:
            c.execute('INSERT INTO operator_startup_reports(source_id,body) VALUES(%s,%s) ON CONFLICT(source_id) DO UPDATE SET body=excluded.body',
                      (self.source_id,Jsonb(body)))

    def startup(self):
        with self.transaction() as c:
            row=c.execute('SELECT body FROM operator_startup_reports WHERE source_id=%s',(self.source_id,)).fetchone()
        return row['body'] if row else None

    def tail(self,stream,*,limit=400):
        if stream not in STREAMS or type(limit) is not int or not 1<=limit<=2000:raise ValueError('bounded log tail required')
        with self.transaction() as c:
            rows=c.execute('SELECT ts,text FROM operator_log_records WHERE stream=%s ORDER BY id DESC LIMIT %s',(stream,limit)).fetchall()
        return list(reversed(rows))

    def append(self,stream,text,*,task_id=None,session_id=None,ts=None,max_bytes=None,max_rows=10000):
        if max_bytes is None:max_bytes=retention(stream)
        if stream not in STREAMS:raise ValueError('fixed operator log stream required')
        if type(max_bytes) is not int or not 1<=max_bytes<=32*1024*1024 or type(max_rows) is not int or not 1<=max_rows<=100000:
            raise ValueError('bounded log retention required')
        for value in (task_id,session_id):
            if value is not None and (not isinstance(value,str) or len(value)>240 or any(ord(c)<32 for c in value)):
                raise ValueError('bounded log identity required')
        stamp=time.time() if ts is None else ts
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0:raise ValueError('finite log timestamp required')
        from .server_log_buffer import scrub
        if stream=='diagnostics':
            import json
            body=json.loads(text)
            if not isinstance(body,dict) or set(body)-{'at','level','logger','message','request_id','task_id','session_id','job_id','error_class','frames'}:
                raise ValueError('fixed diagnostic projection required')
            # Scrub values, never the serialized JSON syntax. Truncating the
            # JSON envelope makes restart-readable diagnostic evidence invalid.
            projected={}
            for key,value in body.items():
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
        raw=text.encode('utf-8')
        if stream=='diagnostics' and len(raw)>min(32768,max_bytes):raise ValueError('bounded diagnostic projection required')
        if len(raw)>min(32768,max_bytes):
            text=raw[:min(32768,max_bytes)-len(b' [truncated]')].decode('utf-8',errors='ignore')+' [truncated]'
        size=len(text.encode('utf-8'))
        if size>max_bytes:raise ValueError('retention too small for log record')
        with self.transaction(checkout_timeout=5) as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operator-log:'+self.source_id+':'+stream,))
            c.execute('INSERT INTO operator_log_records(source_id,stream,ts,text,byte_size,task_id,session_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (self.source_id,stream,stamp,text,size,task_id,session_id))
            c.execute('''DELETE FROM operator_log_records WHERE source_id=%s AND stream=%s AND id IN
                (SELECT id FROM (SELECT id,row_number() OVER(ORDER BY id DESC) n,
                 sum(byte_size) OVER(ORDER BY id DESC) bytes FROM operator_log_records WHERE stream=%s) kept
                 WHERE n>%s OR bytes>%s)''',(self.source_id,stream,stream,max_rows,max_bytes))


def read_operator(reader,owner_store,*,stream=None,task_id=None,limit=400):
    if stream is not None and stream not in STREAMS:raise ValueError('fixed operator log stream required')
    if type(limit) is not int or not 1<=limit<=2000:raise ValueError('bounded operator log page required')
    if task_id is not None and (not isinstance(task_id,str) or not 1<=len(task_id)<=240):raise ValueError('bounded task filter required')
    with reader._read(owner_store,'logs.read') as (c,authority):
        rows=c.execute('''SELECT id,source_id,stream,ts,text,task_id,session_id FROM operator_log_records
            WHERE (%s::text IS NULL OR stream=%s) AND (%s::text IS NULL OR task_id=%s)
            ORDER BY id DESC LIMIT %s''',(stream,stream,task_id,task_id,limit+1)).fetchall()
    return {'scope':'reviewed_cross_user_operator_read','registry_version':authority['registry_version'],
        'records':list(reversed(rows[:limit])),'truncated':len(rows)>limit,'mutations_available':False}


def selected():
    """One stable service source across restarts/hosts, not a generated host ID.

    Separate services may explicitly set a stable source. The ordinary runtime
    intentionally shares one 32 MiB ring, preventing host churn from growing it.
    An explicitly empty/invalid override is an error, never a file fallback.
    """
    import os
    from . import config
    if not config.DATABASE_URL.startswith(('postgres://','postgresql://','host=')):return None
    return PostgresLogs(config.DATABASE_URL,source_id=os.environ.get('DELIVERY_LOG_SOURCE','delivery-runtime'))


import threading
_emitting=threading.local()


# One bounded process writer, sharing the existing process pool. Logging handler
# locks protect enqueue only, never PG round trips. No separate connection pool.
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
            degraded('postgres-logs',error)
        finally:
            _emitting.active=False
            _active_since=None
            _pending.task_done()

def writer_status():
    with _pending.mutex:
        oldest=_active_since or (_pending.queue[0][4] if _pending.queue else None)
        return {'pending':_pending.unfinished_tasks,'capacity':_pending.maxsize,
                'oldest_pending_ms':int((_clock.monotonic()-oldest)*1000) if oldest is not None else 0,
                'acknowledgement':'accepted to bounded memory queue, not durable PG acknowledgement'}

def flush(timeout=5):
    """Bounded shutdown/test drain. Enqueue is not durable success."""
    deadline=_clock.monotonic()+timeout
    while _pending.unfinished_tasks and _clock.monotonic()<deadline:_clock.sleep(.005)
    if _pending.unfinished_tasks:
        from .log_durability import degraded
        degraded('postgres-logs-drain',TimeoutError())
        return False
    return True

def emit(stream,text,**fields):
    """True selects PG (not persisted acknowledgement), never a file fallback.

    Queue overflow/failure is explicit degraded evidence, never silent success.
    """
    global _writer
    from . import config
    if not config.DATABASE_URL.startswith(('postgres://','postgresql://','host=')):return False
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
                _writer=threading.Thread(target=_write_loop,name='pg-evidence-writer',daemon=True);_writer.start()
    except Exception as error:
        from .log_durability import degraded
        degraded('postgres-logs',error)
    return True


def operator_view(reader,owner_store,minutes,*,task_id=None,download=False,now=None):
    """Registry-gated bounded cross-host view. Downloads omit frame/source text."""
    import json
    from datetime import datetime,timezone
    from .server_log_buffer import scrub,EXPORT_MESSAGE_CAP
    now=time.time() if now is None else now
    minutes=max(1,min(int(minutes),240));since=now-minutes*60
    result=reader.logs(owner_store,task_id=task_id,limit=2000)
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
        'scope':'reviewed_cross_user_operator_read','registry_version':result['registry_version'],
        'buffer_full':result['truncated'],'scrubbed':True,
        'since':datetime.fromtimestamp(since,timezone.utc).isoformat(),'until':datetime.fromtimestamp(now,timezone.utc).isoformat(),
        'engine_log':{'available':True,'availability':'available','lines':engine_lines,'path':'postgres:operator_log_records/engine'},
        'storage':'Postgres bounded cross-host process logs; oldest overwritten'}

# Supervisor has no ASGI lifespan; retain its final stdout evidence on normal exit.
import atexit
atexit.register(flush,5)
