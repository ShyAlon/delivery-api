"""Immutable revision checkpoint evidence belongs to the tenant DB, not files."""
import uuid
import pytest
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from test_postgres_store import store,A,B


def test_immutable_snapshot_roundtrip_and_owner_isolation(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('snapshot-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec')
        b=StateGraph(S);b.add_node('next',lambda s:{'count':s['count']+1});b.add_edge(START,'next');b.add_edge('next',END)
        graph=b.compile(checkpointer=saver);cfg={'configurable':{'thread_id':sid}}
        graph.invoke({'count':8},cfg)
        cid=graph.get_state(cfg).config['configurable']['checkpoint_id']
        snapshots=GeneratorSnapshots(store,cap)
        receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=cid,purpose='revision-source')
        assert set(receipt)=={'snapshot_id','sha256','checkpoint_id'}
        graph.invoke({'count':20},cfg)
        loaded=snapshots.read(receipt)
        assert loaded.checkpoint['channel_values']['count']==9
        assert loaded.config['configurable']['checkpoint_id']==cid
        # Snapshot is immutable and address binds original bytes, not saver latest.
        assert snapshots.read(receipt)==loaded
        with pytest.raises(ValueError,match='digest'):snapshots.read(receipt|{'sha256':'0'*64})
        with pytest.raises(ValueError,match='changed'):snapshots.capture(saver,cfg,expected_checkpoint_id=cid,purpose='revision-source')
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):snapshots.read(receipt)

def test_snapshot_storage_is_insert_only_and_claims_cannot_cross_owners(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    import psycopg
    with tenant_context(A):
        tid=store.create_task('snapshot-rls-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2}
        saved=saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        receipt=GeneratorSnapshots(store,cap).capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with store._postgres.transaction() as c:c.execute('UPDATE generator_snapshots SET payload=%s WHERE snapshot_id=%s',(b'changed',receipt['snapshot_id']))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with store._postgres.transaction() as c:c.execute('DELETE FROM generator_snapshots WHERE snapshot_id=%s',(receipt['snapshot_id'],))
    with tenant_context(B):
        with store._postgres.transaction() as c:
            assert c.execute('SELECT * FROM generator_snapshots WHERE snapshot_id=%s',(receipt['snapshot_id'],)).fetchone() is None
    with tenant_context(A):
        with store._postgres.transaction() as c:
            assert c.execute('SELECT sha256 FROM generator_snapshots WHERE snapshot_id=%s',(receipt['snapshot_id'],)).fetchone()['sha256']==receipt['sha256']

def test_snapshot_fork_preserves_graph_schedule_and_parent_isolation(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('snapshot-fork-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        def graph(saver):
            b=StateGraph(S);b.add_node('first',lambda s:{'count':s['count']+1});b.add_node('second',lambda s:{'count':s['count']+10})
            b.add_edge(START,'first');b.add_edge('first','second');b.add_edge('second',END)
            return b.compile(checkpointer=saver,interrupt_before=['second'])
        source=GeneratorCheckpoint(store,cap,jid,'spec');g=graph(source);cfg={'configurable':{'thread_id':sid}}
        assert g.invoke({'count':0},cfg)['count']==1
        state=g.get_state(cfg);assert state.next==('second',)
        snap=GeneratorSnapshots(store,cap);receipt=snap.capture(source,cfg,expected_checkpoint_id=state.config['configurable']['checkpoint_id'],purpose='continuation')
        destination=GeneratorCheckpoint(store,cap,jid,'private:stage',logical_thread_id='future-execution')
        target_cfg={'configurable':{'thread_id':'future-execution'}}
        copied=snap.fork(receipt,destination,target_cfg)
        target=graph(destination)
        assert target.get_state(target_cfg).next==('second',)
        assert target.get_state(target_cfg).values=={'count':1}
        assert target.invoke(None,target_cfg)['count']==11
        assert g.get_state(cfg).values=={'count':1} and g.get_state(cfg).next==('second',)
        assert snap.read(receipt).checkpoint['channel_values']['count']==1
        with pytest.raises(ValueError,match='empty'):snap.fork(receipt,destination,target_cfg)

def test_capture_and_fork_share_single_connection_even_at_pool_cap_one(store,monkeypatch):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-pool-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2}
        saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        # A nested checkout would fail even with spare real pool connections.
        from contextlib import contextmanager
        original=store._postgres.transaction;active=[];checkouts=[]
        @contextmanager
        def single(**kwargs):
            assert not active,'nested DB checkout while task row lock held'
            active.append(True);checkouts.append(True)
            try:
                with original(**kwargs) as c:yield c
            finally:active.pop()
        monkeypatch.setattr(store._postgres,'transaction',single)
        snapshots=GeneratorSnapshots(store,cap)
        receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        destination=GeneratorCheckpoint(store,cap,jid,'private:fork')
        snapshots.fork(receipt,destination,cfg)
        assert len(checkouts)>0

def test_delete_task_retains_immutable_snapshot_and_hides_task(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-delete-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        receipt=GeneratorSnapshots(store,cap).capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        store.finish_worker(jid);store.delete_task(tid)
        assert all(task['task_id']!=tid for task in store.list_tasks())
        with store._postgres.transaction() as c:
            assert c.execute('SELECT sha256 FROM generator_snapshots WHERE snapshot_id=%s',(receipt['snapshot_id'],)).fetchone()['sha256']==receipt['sha256']

def test_snapshot_fork_preserves_pending_task_writes_and_parent_config(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-pending-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2}
        first=saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        second=empty_checkpoint();second['channel_values']={'count':3}
        saved=saver.put(first,second,{'source':'loop','step':1,'parents':{}},{})
        saver.put_writes(saved,[('count',4)],'task-1','node/path')
        snap=GeneratorSnapshots(store,cap);receipt=snap.capture(saver,cfg,expected_checkpoint_id=second['id'],purpose='continuation')
        source=snap.read(receipt);assert source.pending_writes==[('task-1','count',4)]
        target=GeneratorCheckpoint(store,cap,jid,'private:pending')
        snap.fork(receipt,target,cfg)
        result=target.get_tuple(cfg)
        assert result.pending_writes==source.pending_writes
        assert result.parent_config==source.parent_config
        assert target.get_tuple(source.parent_config).checkpoint==cp
        with store._postgres.transaction() as c:
            rows=c.execute('SELECT DISTINCT task_path FROM checkpoint_writes WHERE thread_id=%s AND checkpoint_ns=%s',(sid,target.namespace)).fetchall()
            assert [r['task_path'] for r in rows]==['node/path']

def test_failed_fork_rolls_back_partial_target_even_if_caller_catches(store,monkeypatch):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-rollback-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};saved=saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        saver.put_writes(saved,[('count',3)],'task-1')
        snapshots=GeneratorSnapshots(store,cap);receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        target=GeneratorCheckpoint(store,cap,jid,'private:rollback')
        def fail(*a,**kw):raise RuntimeError('injected pending write failure')
        monkeypatch.setattr(target,'put_writes',fail)
        with store._postgres.transaction() as c:
            token=store._connection.set(c)
            try:
                with pytest.raises(RuntimeError,match='injected'):snapshots.fork(receipt,target,cfg)
                assert target.get_tuple(cfg) is None
            finally:store._connection.reset(token)

def test_private_logical_thread_delta_history_never_uses_physical_thread_as_input(store):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-delta-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        target=GeneratorCheckpoint(store,cap,jid,'private:delta',logical_thread_id='future')
        cfg={'configurable':{'thread_id':'future'}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};cp['channel_versions']={'count':'1'}
        saved=target.put(cfg,cp,{'source':'input','step':0,'parents':{}},cp['channel_versions'])
        assert target.get_delta_channel_history(config=cfg,channels=['count'])==target.get_delta_channel_history(config=saved,channels=['count'])
        assert target.get_delta_channel_history(config=cfg,channels=['count'])=={'count':{'writes':[]}}

def test_snapshot_tables_have_no_customer_or_service_role_acl(store):
    import psycopg
    from test_postgres_store import DSN
    with psycopg.connect(DSN) as c:
        for role in ('anon','authenticated','service_role'):
            for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                assert not c.execute('SELECT has_table_privilege(%s,%s,%s)',(role,'public.generator_snapshots',privilege)).fetchone()[0]
        assert c.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.generator_snapshots'::regclass").fetchone()==(True,True)
        assert c.execute("SELECT has_table_privilege('requi_backend','public.generator_snapshots','SELECT'),has_table_privilege('requi_backend','public.generator_snapshots','INSERT'),has_table_privilege('requi_backend','public.generator_snapshots','UPDATE'),has_table_privilege('requi_backend','public.generator_snapshots','DELETE')").fetchone()==(True,True,False,False)


def test_revoked_auth_session_cannot_read_snapshot_with_copied_capability(store):
    import psycopg
    from test_postgres_store import DSN
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-revoke-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        snapshots=GeneratorSnapshots(store,cap);receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
    try:
        with tenant_context(A):
            with pytest.raises(TenantAccessDenied):snapshots.read(receipt)
            with store._postgres.transaction() as c:assert c.execute('SELECT * FROM generator_snapshots WHERE snapshot_id=%s',(receipt['snapshot_id'],)).fetchone() is None
    finally:
        with psycopg.connect(DSN) as c:c.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(A.session_id,A.owner_id))

def test_immutable_snapshot_reader_reconstructs_real_schedule_after_worker_finishes(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('snapshot-reader-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'private:stage',logical_thread_id='future')
        def graph(backend):
            b=StateGraph(S);b.add_node('first',lambda s:{'count':s['count']+1});b.add_node('second',lambda s:{'count':s['count']+10})
            b.add_edge(START,'first');b.add_edge('first','second');b.add_edge('second',END)
            return b.compile(checkpointer=backend,interrupt_before=['second'])
        cfg={'configurable':{'thread_id':'future'}}
        g=graph(saver);g.invoke({'count':4},cfg);state=g.get_state(cfg)
        snapshots=GeneratorSnapshots(store,cap);receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=state.config['configurable']['checkpoint_id'],purpose='stage')
        store.finish_worker(jid)
        frozen=snapshots.reader(receipt)
        restored=graph(frozen).get_state(cfg)
        assert restored.values=={'count':5} and restored.next==('second',)
        assert restored.config==state.config
        assert len(list(frozen.list(cfg)))>=2
        with pytest.raises(PermissionError,match='immutable'):graph(frozen).invoke(None,cfg)
        with pytest.raises(ValueError,match='identity'):frozen.get_tuple({'configurable':{'thread_id':'foreign'}})


def test_historical_snapshot_is_read_only_after_task_generation_advances(store):
    from delivery_api.generator_snapshot import GeneratorSnapshots
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from delivery_api.historical_checkpoint import HistoricalCheckpointCapability
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('snapshot-history-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        snapshots=GeneratorSnapshots(store,cap);receipt=snapshots.capture(saver,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        with store._postgres.transaction() as c:c.execute('UPDATE tasks SET generation=2 WHERE task_id=%s',(tid,))
        old=HistoricalCheckpointCapability(A,tid,sid,1)
        assert GeneratorSnapshots(store,old).read(receipt).checkpoint['channel_values']=={'count':2}
        reader=GeneratorCheckpoint(store,old,None,'spec',writable=False)
        assert reader.get_tuple(cfg).checkpoint['channel_values']=={'count':2}
        with pytest.raises(PermissionError,match='historical'):GeneratorSnapshots(store,old).capture(reader,cfg,expected_checkpoint_id=cp['id'],purpose='review')
        with pytest.raises(TenantAccessDenied,match='read-only'):GeneratorCheckpoint(store,old,jid,'spec')
