"""Exact-byte PG workflow documents fenced by original worker attempt."""
import hashlib,re,time
from .generator_checkpoint import GeneratorCheckpoint

class GeneratorDocuments:
    def __init__(self,store,capability,job_id,*,expected_attempt=None):
        self.saver=GeneratorCheckpoint(store,capability,job_id,'documents',expected_attempt=expected_attempt)
        self.capability=capability

    @classmethod
    def reader(cls,store,capability):
        reader=cls.__new__(cls);reader.capability=capability
        reader.saver=GeneratorCheckpoint(store,capability,None,'documents',writable=False)
        return reader

    @classmethod
    def owner_reader(cls,store,task_id):
        from .historical_checkpoint import HistoricalCheckpointCapability
        from .tenancy import current_scope,TenantAccessDenied
        task=store.task(task_id)
        if not task:raise TenantAccessDenied('owned document task required')
        sid=task['engine_session_id'];binding=store.session_binding(sid)
        if not binding:raise TenantAccessDenied('owned document execution required')
        return cls.reader(store,HistoricalCheckpointCapability(current_scope(),task_id,sid,binding['generation']))

    def write_internal_cost_rollup(self,raw):
        """Owner-derived report only, never a worker artifact or checkpoint."""
        from .tenancy import TenantAccessDenied
        if type(raw) is not bytes or len(raw)>64*1024*1024:raise ValueError('bounded cost report required')
        with self.saver._cursor() as cur:
            task=cur.execute('SELECT generation,engine_session_id FROM tasks WHERE task_id=%s FOR UPDATE',(self.capability.task_id,)).fetchone()
            if not task or task['generation']!=self.capability.generation or task['engine_session_id']!=self.capability.session_id:
                raise TenantAccessDenied('current cost report binding required')
            self._put_cursor(cur,'cost.json',raw,hashlib.sha256(raw).hexdigest())

    def _require_writer(self):
        if not self.saver.writable:raise PermissionError('read-only workflow document capability')

    @staticmethod
    def _key(key):
        if (not isinstance(key,str) or len(key)>500 or not key
            or any(not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,199}',part) or part in ('.','..') for part in key.split('/'))):
            raise ValueError('canonical owned workflow document key required')
        return key

    @staticmethod
    def _verified_bytes(row):
        raw=bytes(row['payload'])
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('workflow document hash differs')
        return raw

    def _read_parts(self,cur,key,*,not_before=None):
        rows=cur.execute('SELECT * FROM public.requi_document_parts(%s,%s,%s)',(self.capability.session_id,self.capability.generation,key)).fetchall()
        if not rows or (not_before is not None and rows[0]['updated_at']<not_before):return None
        if len(rows)!=rows[0]['total']+1 or any(r['ordinal']!=i for i,r in enumerate(rows)):raise ValueError('workflow chunk sequence differs')
        parts=[]
        for row in rows:
            if row['payload'] is None:
                if row['ordinal']==0 and row['total']==0:return None
                if row['ordinal']==0:continue
                raise ValueError('workflow chunk payload missing')
            parts.append(self._verified_bytes(row))
        return b''.join(parts)

    def read(self,key):
        key=self._key(key)
        with self.saver._cursor() as cur:return self._read_parts(cur,key)

    def read_since(self,key,cursor=None):
        key=self._key(key)
        with self.saver._cursor() as cur:return document_tail(cur,self.capability.session_id,self.capability.generation,key,cursor)

    def recent_lifecycle_events(self,key,artifact_id):
        return recent_lifecycle_events(self,key,artifact_id)

    def read_fresh(self,key,*,not_before):
        key=self._key(key)
        with self.saver._cursor() as cur:return self._read_parts(cur,key,not_before=not_before)

    def capture(self,keys):
        keys=[self._key(key) for key in keys]
        if len(keys)!=len(set(keys)):raise ValueError('unique workflow document keys required')
        with self.saver._cursor() as cur:return self._capture_cursor(cur,keys)

    def _capture_cursor(self,cur,keys):
        return {key:raw for key in keys if (raw:=self._read_parts(cur,key)) is not None}

    def write(self,key,raw):
        self._require_writer()
        key=self._key(key)
        if type(raw) is not bytes or len(raw)>64*1024*1024:raise ValueError('bounded workflow document bytes required')
        with self.saver._cursor() as cur:
            prior=self._capture_cursor(cur,[key]).get(key)
            self._put_cursor(cur,key,raw,hashlib.sha256(raw).hexdigest())
            if key.endswith('.json') and prior!=raw:self._put_cursor(cur,key[:-5]+'.md',None,None)

    def clear(self,key):
        self._require_writer();self._put(self._key(key),None,None)

    def append(self,key,raw):
        self._require_writer()
        key=self._key(key)
        if type(raw) is not bytes:raise ValueError('workflow append bytes required')
        cap=self.capability
        with self.saver._cursor() as cur:
            cur.execute('SELECT public.requi_document_append(%s,%s,%s,%s,%s,%s,%s)',(cap.session_id,cap.generation,key,raw,cap.scope.owner_id,cap.scope.session_id,time.time()))

    def _put(self,key,raw,sha):
        with self.saver._cursor() as cur:self._put_cursor(cur,key,raw,sha)

    def _put_cursor(self,cur,key,raw,sha):
        cap=self.capability
        cur.execute('INSERT INTO generator_documents(execution_session_id,owner_id,auth_session_id,generation,document_key,payload,sha256,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(execution_session_id,document_key) DO UPDATE SET auth_session_id=EXCLUDED.auth_session_id,payload=EXCLUDED.payload,sha256=EXCLUDED.sha256,updated_at=EXCLUDED.updated_at',
            (cap.session_id,cap.scope.owner_id,cap.scope.session_id,cap.generation,key,raw,sha,time.time()))


    def retain_proposal(self,body):
        import json
        proposal_id=body.get('proposal_id')
        if not isinstance(proposal_id,str) or not re.fullmatch('[a-f0-9]{32}',proposal_id):raise ValueError('canonical proposal id required')
        cap=self.capability;wait='pg-revision-proposal:'+proposal_id
        with self.saver._cursor() as cur:
            row=cur.execute('SELECT payload FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision=%s',(cap.task_id,wait,'result')).fetchone()
            if row is not None:
                if json.loads(row['payload'])!=body:raise ValueError('conflicting retained proposal')
                return
            cur.execute('INSERT INTO decision_reviews(task_id,wait_id,revision,payload,created_at) VALUES (%s,%s,%s,%s,%s)',
                (cap.task_id,wait,'result',json.dumps(body),time.time()))

class ChildGeneratorDocuments(GeneratorDocuments):
    """Same exact-byte contract, private child claim/epoch/attempt fencing."""
    def __init__(self,store,claim,binding,*,expected_attempt=None):
        from .generator_checkpoint import ChildGeneratorCheckpoint
        from types import SimpleNamespace
        self.saver=ChildGeneratorCheckpoint(store,claim,binding,'documents',expected_attempt=expected_attempt)
        self.capability=SimpleNamespace(session_id=binding.session_id,generation=binding.generation,
            task_id=binding.task_id,scope=SimpleNamespace(owner_id=str(binding.owner_id),session_id=str(binding.auth_session_id)))



def prepare_document_workspace(store,task_id,documents,*,current_phase):
    """Seed exact published inputs only, never overwrite privately advanced work.

    No import of historical files. A mismatch outside the phase being produced
    refuses instead of blessing a replacement or silently discarding evidence.
    """
    from .artifact_publication import document_artifact_keys
    if documents.capability.task_id!=task_id:raise ValueError('document task differs')
    keys=document_artifact_keys();keys[('baseline','system-baseline.json')]='spec/system-baseline.json'
    with documents.saver._cursor() as cursor:
        snapshot=store.manifest_snapshot(task_id,documents.capability.generation)
        if snapshot is None:return
        existing=documents._capture_cursor(cursor,list(keys.values()))
        for row in snapshot['rows']:
            identity=(row['phase_key'],row['name']);key=keys.get(identity)
            if key is None:continue
            raw=store.read_artifact_bytes_sha(row['sha256'])
            if key in existing:
                if existing[key]!=raw and row['phase_key']!=current_phase:
                    raise ValueError('settled document differs from published manifest: '+key)
                continue
            documents._put_cursor(cursor,key,raw,hashlib.sha256(raw).hexdigest())


def document_artifact_snapshot(documents,phases=None):
    from .artifact_publication import document_artifact_keys
    keys={identity:key for identity,key in document_artifact_keys().items() if phases is None or identity[0] in phases}
    captured=documents.capture(list(keys.values()))
    return {identity:hashlib.sha256(captured[key]).hexdigest() for identity,key in keys.items() if key in captured}


def document_set_hash(documents):
    from .artifact_publication import artifact_set_hash
    return artifact_set_hash(document_artifact_snapshot(documents))


def pin_document_artifacts(task,documents,*,current_phase):
    from .artifact_publication import settled_phases
    settled=settled_phases(task,current_phase)
    return document_artifact_snapshot(documents,settled),settled


def document_substitutions(documents,pinned,settled):
    from .artifact_publication import snapshot_substitutions
    return snapshot_substitutions(pinned,document_artifact_snapshot(documents,settled))


def commit_gate_documents(store,task_id,phase,replacements):
    """Narrow founder-decision writer, separate from any worker authority.

    Existing endpoint checks own review/revision/choice semantics. This boundary
    requires the exact current waiting phase and atomically replaces documents,
    provisional lineage and the ready manifest. No file import or graph mutation.
    """
    import json
    from .artifact_publication import publish_preserving_revision
    from .worker_capability import WorkerCapability
    from .phases import PHASE_BY_KEY
    from .tenancy import TenantAccessDenied
    if phase not in PHASE_BY_KEY or not replacements or any(name not in PHASE_BY_KEY[phase].artifacts for name in replacements):
        raise ValueError('declared gate artifact required')
    with store._task_operation_guard('task.gate.decide'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        cap=WorkerCapability.capture(store,task_id);task=cap.validate(store)
        gate=next((g for g in task['gates'] if g['after_phase']==phase),None)
        row=next((p for p in task['phases'] if p['phase_key']==phase),None)
        if not gate or gate['state']!='waiting' or not row or row['state']!='awaiting_approval':raise ValueError('exact waiting gate required')
        if task.get('stop_requested_at') is not None:raise TenantAccessDenied('gate execution stopped')
        if store._execute_native("SELECT 1 FROM worker_jobs WHERE task_id=%s AND generation=%s AND state='running' LIMIT 1",(task_id,cap.generation)).fetchone():
            from .gate_job_reconciliation import reconcile
            try:reconcile(lambda sql,params=():store._execute_native(sql,params,write=sql.startswith(('UPDATE','INSERT'))),task_id,phase,apply=True)
            except ValueError as exc:raise ValueError('gate producer cannot be cleared: '+str(exc)) from exc
            if store._execute_native("SELECT 1 FROM worker_jobs WHERE task_id=%s AND generation=%s AND state='running' LIMIT 1",(task_id,cap.generation)).fetchone():
                raise ValueError('gate producer is still running')
        reader=GeneratorDocuments.reader(store,cap)
        with reader.saver._cursor() as cursor:
            lineage_raw=reader._capture_cursor(cursor,['spec/lineage.json']).get('spec/lineage.json')
            record=json.loads(lineage_raw) if lineage_raw is not None else None
            if record is not None and (not isinstance(record,dict) or not isinstance(record.get('entries'),list)):
                raise ValueError('invalid lineage authority record')
            for name,raw in replacements.items():
                if type(raw) is not bytes or len(raw)>64*1024*1024:raise ValueError('bounded gate document bytes required')
                key='spec/'+name;sha=hashlib.sha256(raw).hexdigest()
                reader._put_cursor(cursor,key,raw,sha);reader._put_cursor(cursor,key[:-5]+'.md',None,None)
                if record is not None:
                    generator_phase='items' if phase=='specs' else phase
                    record['entries']=[entry for entry in record['entries'] if (entry['phase'],entry['name'])!=(generator_phase,name)]
                    record['entries'].append({'phase':generator_phase,'name':name,'sha256':sha,'source':'founder-decision','settled':False,
                        'task_id':task_id,'session_id':cap.session_id,'generation':str(cap.generation)})
            if record is not None:
                raw=json.dumps(record,indent=2,sort_keys=True).encode();reader._put_cursor(cursor,'spec/lineage.json',raw,hashlib.sha256(raw).hexdigest())
            publish_preserving_revision(store,task_id,{(phase,name):raw for name,raw in replacements.items()})


def transition_gate_lineage(store,task_id,phase,*,settled):
    """Decision-time lineage transition under the owner's task transaction."""
    import json
    from .worker_capability import WorkerCapability
    with store._task_operation_guard('task.gate.decide'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        cap=WorkerCapability.capture(store,task_id);cap.validate(store)
        reader=GeneratorDocuments.reader(store,cap)
        with reader.saver._cursor() as cursor:
            raw=reader._capture_cursor(cursor,['spec/lineage.json']).get('spec/lineage.json')
            record=json.loads(raw) if raw is not None else {'version':1,'entries':[],'inputs':{}}
            if not isinstance(record,dict) or not isinstance(record.get('entries'),list):raise ValueError('invalid lineage authority record')
            order=['needs','srs','architecture','items','tests','validation'];phase='items' if phase=='specs' else phase
            if phase not in order:return
            if settled:
                for entry in record['entries']:
                    if entry['phase']==phase:entry['settled']=True
            else:
                invalid=set(order[order.index(phase):]);record['entries']=[entry for entry in record['entries'] if entry['phase'] not in invalid]
                from .phases import PHASE_DEFINITIONS
                for definition in PHASE_DEFINITIONS:
                    if definition.generator_stage in invalid:
                        for name in definition.artifacts:
                            reader._put_cursor(cursor,'spec/'+name,None,None)
                            reader._put_cursor(cursor,'spec/'+name[:-5]+'.md',None,None)
                partials={'srs':'srs_fills.partial.json','architecture':'architecture.partial.json','items':'items.partial.json','tests':'tests_fills.partial.json','validation':'validation_fills.partial.json'}
                for stage in invalid:
                    if stage in partials:reader._put_cursor(cursor,'spec/'+partials[stage],None,None)
            raw=json.dumps(record,indent=2,sort_keys=True).encode();reader._put_cursor(cursor,'spec/lineage.json',raw,hashlib.sha256(raw).hexdigest())

def document_tail(cur,sid,generation,key,cursor=None):
    """Exact append delta; overwrite/clear changes epoch and requires one reset."""
    row=cur.execute('SELECT chunk_epoch,chunk_count FROM generator_documents WHERE execution_session_id=%s AND generation=%s AND document_key=%s FOR SHARE',(sid,generation,key)).fetchone()
    if row is None:return None,None,True
    reset=cursor is None or cursor[0]!=row['chunk_epoch'] or cursor[1]>row['chunk_count']
    after=0 if reset else cursor[1];parts=[]
    if reset:
        base=cur.execute('SELECT payload,sha256 FROM generator_documents WHERE execution_session_id=%s AND generation=%s AND document_key=%s',(sid,generation,key)).fetchone()
        if base['payload'] is not None:parts.append(GeneratorDocuments._verified_bytes(base))
    chunks=cur.execute('SELECT payload,sha256,ordinal FROM document_chunks WHERE execution_session_id=%s AND document_key=%s AND epoch=%s AND ordinal>%s ORDER BY ordinal',(sid,key,row['chunk_epoch'],after)).fetchall()
    if len(chunks)!=row['chunk_count']-after or any(c['ordinal']!=after+i+1 for i,c in enumerate(chunks)):raise ValueError('workflow chunk sequence differs')
    parts.extend(GeneratorDocuments._verified_bytes(c) for c in chunks)
    return b''.join(parts),[row['chunk_epoch'],row['chunk_count']],reset


def recent_lifecycle_events(documents,key,artifact_id):
    """Durable latest-two lifecycle projection, refreshed from verified new chunks."""
    import json,copy
    from psycopg.types.json import Jsonb
    cap=documents.capability;key=documents._key(key)
    with documents.saver._cursor() as cur:
        # Common order with normal worker writes and owner-derived usage updates.
        cur.execute('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(cap.task_id,)).fetchone()
        saved=cur.execute('SELECT body FROM document_projections WHERE execution_session_id=%s AND document_key=%s',(cap.session_id,key)).fetchone()
        state=saved['body'] if saved else {'cursor':None,'pending':'','events':[],'artifact_id':artifact_id}
        if state.get('artifact_id')!=artifact_id:raise ValueError('lifecycle projection artifact differs')
        raw,cursor,reset=document_tail(cur,cap.session_id,cap.generation,key,state['cursor'])
        if reset:state={'cursor':None,'pending':'','events':[],'artifact_id':artifact_id}
        def consume(target,line):
            if line.strip():
                value=json.loads(line)
                if value.get('artifact_id')==artifact_id:target['events']=(target['events']+[value])[-2:]
        if raw is not None:
            data=bytes.fromhex(state['pending'])+raw;state['pending']='';lines=data.splitlines(keepends=True)
            if lines and not lines[-1].endswith((b'\n',b'\r')):state['pending']=lines.pop().hex()
            for line in lines:consume(state,line)
            state['cursor']=cursor
            if not saved or raw or reset:
                cur.execute('INSERT INTO document_projections(execution_session_id,document_key,owner_id,generation,body) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(execution_session_id,document_key) DO UPDATE SET body=EXCLUDED.body',(cap.session_id,key,cap.scope.owner_id,cap.generation,Jsonb(state)))
        transient=copy.deepcopy(state)
        if transient['pending']:consume(transient,bytes.fromhex(transient['pending']))
        return transient['events']
