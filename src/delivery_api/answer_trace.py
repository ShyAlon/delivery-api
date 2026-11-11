"""In-memory operation tracing. No SQL, parameters or project text is retained."""
import collections,json,os,sys,threading,time
from contextvars import ContextVar
_active=ContextVar('answer_wire_trace',default=None)
_install_lock=threading.Lock()
_original_wait=None
_installed=set()
_process_trace=None


def _measured_wait(conn,generator,*args,**kw):
    trace=_active.get() or _process_trace
    original=_original_wait
    if trace is None:return original(conn,generator,*args,**kw)
    return trace._wait(original,conn,generator,*args,**kw)

from contextlib import contextmanager


def caller():
    frame=sys._getframe(2);names=[]
    while frame is not None and (len(names)<3 or (len(names)==3 and names[-1]=='postgres_store:_execute_native')):
        module=frame.f_globals.get('__name__','')
        if module.startswith(('delivery_api.','interview_engine.','langgraph.checkpoint.postgres')) and not module.endswith('answer_trace'):
            names.append(module.rsplit('.',1)[-1]+':'+frame.f_code.co_name)
        frame=frame.f_back
    return '<'.join(names) or 'database:other'


class WireTrace:
    """Count actual outgoing Sync/simple Query barriers in an operation.

    libpq protocol bytes pass only through an anonymous pipe while wait runs.
    They are discarded before returning. Only counts, duration and code callers
    survive. Startup/auth packets are not query barriers. Pool liveness is included.
    """
    def __init__(self):self.rows=collections.defaultdict(lambda:[0,0,0]);self.lock=threading.Lock();self.failed=False
    def reset(self):
        with self.lock:self.rows.clear();self.failed=False
    def snapshot(self):
        with self.lock:return [{'caller':k,'waits':v[0],'syncs':v[1],'duration_ms':round(v[2],3)} for k,v in sorted(self.rows.items())]
    def _wait(self,original,conn,generator,*args,**kw):
        # The macOS owner proof has no memfd_create. A pipe drains protocol
        # bytes concurrently, so it needs neither disk nor a Linux-only fd.
        name=caller();began=time.monotonic();fd=None;reader=None;thread=None;count=None;captured=[0,True]
        def drain():
            pending=b''
            try:
                while True:
                    chunk=os.read(reader,65536)
                    if not chunk:break
                    parts=(pending+chunk).split(b'\n');pending=parts.pop()
                    for line in parts:
                        captured[0]+=int(b'\tF\t' in line and (b'\tSync' in line or b'\tQuery\t' in line))
                    # Query payloads can be large; only the bounded protocol
                    # record header is relevant. Never retain raw SQL/text.
                    if len(pending)>262144:pending=pending[:512]
                if pending:
                    captured[0]+=int(b'\tF\t' in pending and (b'\tSync' in pending or b'\tQuery\t' in pending))
            except OSError:captured[1]=False
        try:
            try:
                reader,fd=os.pipe()
                thread=threading.Thread(target=drain,daemon=True,name='answer-wire-drain');thread.start()
                conn.pgconn.trace(fd)
            except (OSError,AttributeError):
                return original(conn,generator,*args,**kw)
            try:return original(conn,generator,*args,**kw)
            finally:
                try:conn.pgconn.untrace()
                except (OSError,AttributeError):captured[1]=False
                os.close(fd);fd=None
                thread.join()
                if captured[1]:count=captured[0]
        finally:
            if fd is not None:os.close(fd)
            if thread is not None and thread.is_alive():thread.join()
            if reader is not None:os.close(reader)
            with self.lock:
                row=self.rows[name];row[0]+=1;row[1]+=count or 0;row[2]+=(time.monotonic()-began)*1000
                if count is None:self.failed=True
    def install(self,*,context_only=False):
        import psycopg
        global _original_wait,_process_trace
        token=_active.set(self)
        with _install_lock:
            if not _installed:
                _original_wait=psycopg.Connection.wait
                psycopg.Connection.wait=_measured_wait
            _installed.add(self)
            if not context_only:_process_trace=self
        def restore():
            global _original_wait,_process_trace
            _active.reset(token)
            with _install_lock:
                _installed.discard(self)
                if _process_trace is self:_process_trace=None
                if not _installed:
                    psycopg.Connection.wait=_original_wait
                    _original_wait=None
        self.restore=restore
    def emit(self,operation_id):
        for row in self.snapshot():print('REQUI_ANSWER_TRACE '+json.dumps({'operation_id':operation_id,**row},separators=(',',':')),file=sys.stderr,flush=True)
        print('REQUI_ANSWER_TRACE '+json.dumps({'operation_id':operation_id,'complete':not self.failed},separators=(',',':')),file=sys.stderr,flush=True)


def tables(entries):
    from .statement_trace import phase_of
    callers=collections.defaultdict(lambda:[0,0,0]);phases=collections.defaultdict(lambda:[0,0,0])
    for where,shape,ms,sync in entries:
        for row in (callers[where],phases[phase_of(where)]):row[0]+=1;row[1]+=sync;row[2]+=ms
    def rows(values):return [{'name':k,'statements':v[0],'syncs':v[1],'duration_ms':v[2]} for k,v in sorted(values.items())]
    return {'callers':rows(callers),'phases':rows(phases),'syncs':sum(v[1] for v in callers.values())}


def flush(value):
    """One final metadata row plus bounded caller/phase rows in the existing log.
    No database writes. Each row stays below the log scrubber's line bound.
    """
    from .process_logs import emit
    from uuid import uuid5,NAMESPACE_URL
    identity={'trace_id':str(uuid5(NAMESPACE_URL,value['job_id'])),'session_id':value['session_id']}
    def write(row):emit('engine','answer.trace '+json.dumps({**identity,**row},separators=(',',':')),session_id=value['session_id'])
    for key in ('duration_ms','error_class','total_syncs'):write({'category':'summary','field':key,'value':value[key]})
    for key,item in value['accounting'].items():
        if key!='answer_id':write({'category':'accounting','field':key,'value':item})
    for category in ('phases','callers'):
        for row in value['parent'][category]:write({'source':'parent','category':category,**row})
    for row in value.get('parent_wire',[]):write({'source':'parent_wire','category':'callers',**row})
    write({'source':'parent_wire','complete':value.get('parent_wire_complete',False)})
    for sid,stage,label,status in value.get('engine_timings',[]):
        write({'source':'engine_timing','stage':stage,'label':label[:120],'status':status})
    child=value.get('child')
    write({'source':'child','complete':child['complete'] if child else False,'syncs':sum(r['syncs'] for r in child['callers'].values()) if child and child['complete'] else None})
    if child:
        phases=collections.defaultdict(lambda:[0,0])
        for name,row in child['callers'].items():
            write({'source':'child','category':'callers','name':name,**row})
            phase='checkpoint' if any(x in name for x in ('checkpoint','postgres:')) else 'session_persistence'
            phases[phase][0]+=row['syncs'];phases[phase][1]+=row['duration_ms']
        for name,row in phases.items():write({'source':'child','category':'phases','name':name,'syncs':row[0],'duration_ms':round(row[1],3)})


def exported_lines():
    from .process_logs import FileLogs,flush as drain
    drain()
    rows=FileLogs().records(stream='engine',limit=2000)
    return '\n'.join(r['text'] for r in rows['records'] if r['text'].startswith('answer.trace '))
