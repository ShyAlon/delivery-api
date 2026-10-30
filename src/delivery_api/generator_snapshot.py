"""Immutable checkpoint evidence, not a live graph cursor or execution grant."""
import hashlib,re,time,uuid
from contextlib import contextmanager
from langgraph.checkpoint.base import CheckpointTuple

class GeneratorSnapshots:
    def __init__(self,store,capability):
        self.store=store;self.capability=capability
        from spec_generator.graph import checkpoint_serde
        self.serde=checkpoint_serde()

    @contextmanager
    def _connection(self):
        current=self.store._connection.get()
        if current is not None:yield current
        else:
            with self.store._postgres.transaction() as c:yield c

    @contextmanager
    def _transaction(self,*,read_only=False):
        cap=self.capability
        from .historical_checkpoint import HistoricalCheckpointCapability
        historical=isinstance(cap,HistoricalCheckpointCapability)
        if historical and not read_only:raise PermissionError('historical snapshot is read-only')
        cap.validate(self.store)
        with self._connection() as c,c.transaction():
            token=self.store._connection.set(c)
            try:
                row=c.execute('SELECT generation,engine_session_id,stop_requested_at FROM tasks WHERE task_id=%s FOR UPDATE',(cap.task_id,)).fetchone()
                if (not row or (not historical and (row['generation']!=cap.generation or row['engine_session_id']!=cap.session_id or row['stop_requested_at'] is not None))):
                    raise ValueError('snapshot task authority changed')
                cap.validate(self.store)
                yield c
            finally:self.store._connection.reset(token)

    def capture(self,saver,config,*,expected_checkpoint_id,purpose):
        if not isinstance(purpose,str) or not re.fullmatch('[a-z][a-z0-9-]{0,79}',purpose):raise ValueError('bounded snapshot purpose required')
        cap=self.capability
        from .generator_checkpoint import GeneratorCheckpoint,ChildGeneratorCheckpoint
        if (not isinstance(saver,GeneratorCheckpoint) or isinstance(saver,ChildGeneratorCheckpoint)
            or saver.store is not self.store or saver.capability!=cap):raise ValueError('matching owner checkpoint saver required')
        # Generator saver reuses this scoped connection. Holding the same task
        # row lock across read/serialization/insert makes this an atomic snapshot
        # against all legitimate stop/attempt/checkpoint writers.
        with self._transaction() as c:
            row=saver.get_tuple(config)
            if row is None or row.config['configurable']['checkpoint_id']!=expected_checkpoint_id:raise ValueError('checkpoint changed before snapshot')
            chain=[];current=row;seen=set()
            while current is not None:
                cid=current.config['configurable']['checkpoint_id']
                if cid in seen or len(chain)>=10000:raise ValueError('bounded acyclic checkpoint ancestry required')
                seen.add(cid);chain.append({k:getattr(current,k) for k in CheckpointTuple._fields})
                writes=c.execute('SELECT task_id,task_path FROM checkpoint_writes WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s',
                    (cap.session_id,saver.namespace,cid)).fetchall()
                paths={}
                for write in writes:
                    if write['task_id'] in paths and paths[write['task_id']]!=write['task_path']:raise ValueError('ambiguous checkpoint task path')
                    paths[write['task_id']]=write['task_path']
                chain[-1]['_task_paths']=paths
                parent=current.parent_config
                current=saver.get_tuple(parent) if parent else None
                if parent is not None and current is None:raise ValueError('checkpoint parent missing')
            encoding,raw=self.serde.dumps_typed({'chain':chain})
            if len(raw)>64*1024*1024:raise ValueError('bounded checkpoint snapshot required')
            digest=hashlib.sha256(encoding.encode()+b'\0'+raw).hexdigest();sid=uuid.uuid4().hex
            if saver.get_tuple(config)!=row:raise ValueError('checkpoint changed across snapshot')
            c.execute('INSERT INTO generator_snapshots(snapshot_id,owner_id,auth_session_id,task_id,generation,execution_session_id,purpose,checkpoint_id,encoding,payload,sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (sid,cap.scope.owner_id,cap.scope.session_id,cap.task_id,cap.generation,cap.session_id,purpose,expected_checkpoint_id,encoding,raw,digest,time.time()))
        return dict(snapshot_id=sid,sha256=digest,checkpoint_id=expected_checkpoint_id)

    def _retain_prepared(self,saver,config,*,purpose):
        """Internal staging evidence only, never grants a live checkpoint writer.

        Stage coordinator owns typed source/lineage validation while task lock
        is held. No model calls execute in this memory-only graph.
        """
        from langgraph.checkpoint.memory import MemorySaver
        if not isinstance(saver,MemorySaver):raise ValueError('isolated stage graph required')
        cap=self.capability
        with self._transaction() as c:
            chain=[];row=saver.get_tuple(config)
            if row is None:raise ValueError('prepared stage missing')
            cid=row.config['configurable']['checkpoint_id']
            while row is not None:
                value={k:getattr(row,k) for k in CheckpointTuple._fields};value['_task_paths']={}
                if row.pending_writes:raise ValueError('prepared stage must have no pending execution writes')
                chain.append(value)
                if len(chain)>10000:raise ValueError('bounded stage ancestry required')
                row=saver.get_tuple(row.parent_config) if row.parent_config else None
            encoding,raw=self.serde.dumps_typed({'chain':chain})
            if len(raw)>64*1024*1024:raise ValueError('bounded stage checkpoint required')
            digest=hashlib.sha256(encoding.encode()+b'\0'+raw).hexdigest();sid=uuid.uuid4().hex
            c.execute('INSERT INTO generator_snapshots(snapshot_id,owner_id,auth_session_id,task_id,generation,execution_session_id,purpose,checkpoint_id,encoding,payload,sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (sid,cap.scope.owner_id,cap.scope.session_id,cap.task_id,cap.generation,cap.session_id,purpose,cid,encoding,raw,digest,time.time()))
            return dict(snapshot_id=sid,sha256=digest,checkpoint_id=cid)

    def read(self,receipt,*,_chain=False):
        if not isinstance(receipt,dict) or set(receipt)!={'snapshot_id','sha256','checkpoint_id'}:raise ValueError('exact snapshot receipt required')
        cap=self.capability
        with self._transaction(read_only=True) as c:
            row=c.execute('SELECT * FROM generator_snapshots WHERE snapshot_id=%s AND task_id=%s AND generation=%s AND execution_session_id=%s',
                (receipt['snapshot_id'],cap.task_id,cap.generation,cap.session_id)).fetchone()
            if row is None:raise ValueError('owned checkpoint snapshot missing')
            raw=bytes(row['payload']);digest=hashlib.sha256(row['encoding'].encode()+b'\0'+raw).hexdigest()
            if digest!=row['sha256'] or digest!=receipt['sha256'] or row['checkpoint_id']!=receipt['checkpoint_id']:raise ValueError('snapshot digest differs')
            chain=self.serde.loads_typed((row['encoding'],raw))['chain']
            paths=[value.pop('_task_paths') for value in chain]
            for value in chain:value['pending_writes']=[tuple(write) for write in value.get('pending_writes') or []]
            if chain[0]['config']['configurable']['checkpoint_id']!=receipt['checkpoint_id']:raise ValueError('snapshot checkpoint identity differs')
            return list(zip([CheckpointTuple(**value) for value in chain],paths)) if _chain else CheckpointTuple(**chain[0])

    def fork(self,receipt,destination,config):
        """Copy immutable graph execution state to an empty private namespace.

        Preserves versions, scheduling channels and pending writes. This does
        not install a session or grant authority; destination saver still fences
        every write under the owning source capability.
        """
        from copy import deepcopy
        if (not isinstance(config,dict) or set(config)!={'configurable'}
            or set(config['configurable'])!={'thread_id'}):raise ValueError('fresh logical thread-only destination required')
        from .generator_checkpoint import GeneratorCheckpoint,ChildGeneratorCheckpoint
        if (not isinstance(destination,GeneratorCheckpoint) or isinstance(destination,ChildGeneratorCheckpoint)
            or destination.store is not self.store or destination.capability!=self.capability
            or not destination.namespace.startswith('generator:private:')):raise ValueError('matching private owner checkpoint required')
        with self._transaction():
            chain=self.read(receipt,_chain=True)
            if destination.get_tuple(config) is not None:raise ValueError('fork destination must be empty')
            for item,paths in reversed(chain):
                cp=deepcopy(item.checkpoint)
                parent=dict(config);parent['configurable']=dict(config['configurable'])
                if item.parent_config:parent['configurable']['checkpoint_id']=item.parent_config['configurable']['checkpoint_id']
                saved=destination.put(parent,cp,deepcopy(item.metadata),cp['channel_versions'])
                grouped={}
                for task_id,channel,value in item.pending_writes or []:
                    grouped.setdefault(task_id,[]).append((channel,value))
                for task_id,writes in grouped.items():destination.put_writes(saved,writes,task_id,paths[task_id])
                observed=destination.get_tuple(saved)
                if observed.checkpoint!=item.checkpoint or observed.pending_writes!=item.pending_writes:
                    raise ValueError('fork checkpoint evidence differs')
            return destination.get_tuple(config).config

    def reader(self,receipt):
        return FrozenSnapshot(self,dict(receipt))


from langgraph.checkpoint.base import BaseCheckpointSaver

class FrozenSnapshot(BaseCheckpointSaver):
    """Read-only graph view; every operation rechecks owner + receipt in PG."""
    def __init__(self,snapshots,receipt):
        super().__init__(serde=snapshots.serde)
        self.snapshots=snapshots;self.receipt=receipt

    def _rows(self,config):
        rows=[row for row,paths in self.snapshots.read(self.receipt,_chain=True)]
        cfg=(config or {}).get('configurable',{})
        if (cfg.get('thread_id')!=rows[0].config['configurable']['thread_id']
            or cfg.get('checkpoint_ns','')!=''):raise ValueError('snapshot logical identity differs')
        return rows

    def get_tuple(self,config):
        rows=self._rows(config);cid=config['configurable'].get('checkpoint_id')
        return next((row for row in rows if cid is None or row.config['configurable']['checkpoint_id']==cid),None)

    def list(self,config,*,filter=None,before=None,limit=None):
        rows=self._rows(config)
        if before is not None:self._rows(before)
        n=0
        for row in rows:
            cid=row.config['configurable']['checkpoint_id']
            if before and cid>=before['configurable']['checkpoint_id']:continue
            if filter and any(row.metadata.get(k)!=v for k,v in filter.items()):continue
            if limit is not None and n>=limit:break
            n+=1;yield row

    def get_next_version(self,*args,**kwargs):raise PermissionError('immutable snapshot has no write authority')
    def put(self,*args,**kwargs):raise PermissionError('immutable snapshot has no write authority')
    def put_writes(self,*args,**kwargs):raise PermissionError('immutable snapshot has no write authority')
    def delete_thread(self,*args,**kwargs):raise PermissionError('immutable snapshot has no delete authority')
