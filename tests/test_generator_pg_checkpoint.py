"""Generator saver must fence each PG checkpoint with durable job authority."""
import uuid,pytest
from test_postgres_store import store,A,B,DSN
from delivery_api.tenancy import tenant_context,TenantAccessDenied

def test_generator_state_survives_new_saver_and_namespace_isolation(store,tmp_path):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('pg-generator-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(jid)
        def graph(namespace):
            saver=GeneratorCheckpoint(store,cap,jid,namespace)
            b=StateGraph(S);b.add_node('next',lambda s:{'count':s['count']+1});b.add_edge(START,'next');b.add_edge('next',END)
            return b.compile(checkpointer=saver)
        cfg={'configurable':{'thread_id':sid}}
        assert graph('spec').invoke({'count':1},cfg)['count']==2
        assert graph('spec').get_state(cfg).values=={'count':2}
        assert graph('proposal').get_state(cfg).values=={}
        store.finish_worker(jid)
        with pytest.raises(TenantAccessDenied):graph('spec').invoke({'count':4},cfg)
    assert list(tmp_path.iterdir())==[]

def test_generator_saver_refuses_wrong_thread_and_revoked_job(store):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    with tenant_context(A):
        tid=store.create_task('pg-generator-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec')
        with pytest.raises(TenantAccessDenied):saver.get_tuple({'configurable':{'thread_id':'wrong'}})
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):saver.get_tuple({'configurable':{'thread_id':sid}})

def test_host_checkpoint_factory_avoids_sqlite_for_real_graph(store,tmp_path,monkeypatch):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from spec_generator.checkpoint_runtime import checkpoint,read_state
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('pg-generator-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(jid)
        root=tmp_path/'tenants'/A.owner_id/'sessions'/sid;(root/'spec').mkdir(parents=True)
        r=JobRuntime({'needs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,
            checkpoint_factory=lambda ns:GeneratorCheckpoint(store,cap,jid,ns))
        r.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=jid,generation=cap.generation,session_root=root)
        import sqlite3
        monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('SQLite forbidden')))
        with job_runtime(r):
            saver=checkpoint(root/'spec');b=StateGraph(S);b.add_node('next',lambda s:{'count':s['count']+1});b.add_edge(START,'next');b.add_edge('next',END)
            graph=b.compile(checkpointer=saver);cfg={'configurable':{'thread_id':sid}}
            assert graph.invoke({'count':3},cfg)=={'count':4}
            cid=graph.get_state(cfg).config['configurable']['checkpoint_id']
            assert read_state(root/'spec',session_id=sid,expected_checkpoint_id=cid)['count']==4
            assert not (root/'spec/checkpoints.sqlite').exists()
            with pytest.raises(ValueError):checkpoint(tmp_path/'outside')
            with pytest.raises(ValueError):read_state(root/'spec',session_id=sid,expected_checkpoint_id='stale')

def test_child_saver_requires_live_claim_and_current_worker(store):
    from types import SimpleNamespace
    from delivery_api.generator_checkpoint import ChildGeneratorCheckpoint
    from interview_engine.pg_session_store import PgSessionStore
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class S(TypedDict):count:int
    with tenant_context(A):
        tid=store.create_task('pg-child-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        binding=SimpleNamespace(session_id=sid,owner_id=A.owner_id,task_id=tid,job_id=jid,generation=cap.generation,auth_session_id=A.session_id)
        claim=pg.claim(sid,A.owner_id,'child-test',60)
        saver=ChildGeneratorCheckpoint(pg,claim,binding,'spec');saver.setup()
        b=StateGraph(S);b.add_node('next',lambda s:{'count':s['count']+1});b.add_edge(START,'next');b.add_edge('next',END)
        g=b.compile(checkpointer=saver);cfg={'configurable':{'thread_id':sid}}
        assert g.invoke({'count':5},cfg)['count']==6
        assert pg.release(claim)
        with pytest.raises(TenantAccessDenied,match='authority lost'):saver.get_tuple(cfg)
        claim2=pg.claim(sid,A.owner_id,'child-test-new',60)
        fresh=ChildGeneratorCheckpoint(pg,claim2,binding,'spec')
        assert fresh.get_tuple(cfg).checkpoint['channel_values']['count']==6
        store.finish_worker(jid)
        with pytest.raises(TenantAccessDenied,match='authority lost'):fresh.get_tuple(cfg)
        pg.release(claim2)

def test_delayed_factory_cannot_adopt_retried_attempt(store):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    with tenant_context(A):
        tid=store.create_task('pg-attempt-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        attempt=store.worker_job(jid)['attempt']
        saver=GeneratorCheckpoint(store,cap,jid,'spec',expected_attempt=attempt)
        store.pause_interrupted_worker(jid);assert store.claim_worker(jid)
        with pytest.raises(TenantAccessDenied,match='attempt changed'):GeneratorCheckpoint(store,cap,jid,'proposal',expected_attempt=attempt)
        with pytest.raises(TenantAccessDenied,match='authority lost'):saver.get_tuple({'configurable':{'thread_id':sid}})

def test_real_revision_runner_uses_pg_checkpoints_without_source_mutation(store,tmp_path,monkeypatch):
    import json
    from copy import deepcopy
    from pathlib import Path
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from spec_generator.stop_fence import StopFence
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.revision_worker import run_revision_proposal
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.graph import StateGraph,END
    from typing import TypedDict
    class S(TypedDict,total=False):
        intent_model:dict
        needs:dict
        system_baseline:dict
        conops:dict
        srs:dict
        gate_feedback:dict
    with tenant_context(A):
        tid=store.create_task('pg-revision-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        root=tmp_path/'tenants'/A.owner_id/'sessions'/sid;(root/'spec').mkdir(parents=True);(root/'interview').mkdir()
        original=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())
        for k in ('intent_model','needs','srs','conops'):original[k]['session_id']=sid
        original['srs']['intent_model_ref']=sid
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cp=empty_checkpoint();cp['channel_values']=deepcopy(original);cp['channel_versions']={k:1 for k in original}
        cfg={'configurable':{'thread_id':sid}}
        saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},cp['channel_versions'])
        def factory(log,checkpointer,**kwargs):
            b=StateGraph(S);b.add_node('needs',lambda s:{})
            def srs(s):
                assert s['gate_feedback']['srs']=='Describe offline behavior'
                return {'srs':deepcopy(original['srs']),'gate_feedback':{}}
            b.add_node('srs',srs);b.set_entry_point('needs');b.add_edge('needs','srs');b.add_edge('srs',END)
            return b.compile(checkpointer=checkpointer)
        runtime=JobRuntime({'srs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,
            checkpoint_factory=lambda ns:GeneratorCheckpoint(store,cap,jid,ns))
        runtime.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=jid,generation=cap.generation,session_root=root)
        import sqlite3
        monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('SQLite forbidden')))
        before=saver.get_tuple(cfg)
        with job_runtime(runtime),cli_execution(StopFence(root/'interview/stop-request.json'),None):
            proposal=run_revision_proposal(capability=cap,session_root=root,checkpoint_id=cp['id'],phase='srs',
                change_request='Describe offline behavior',validate_authority=lambda:cap.validate(store),log=None,graph_factory=factory)
        assert not proposal['committed'] and proposal['requires_fresh_review']
        assert saver.get_tuple(cfg)==before
        assert not list(root.rglob('*.sqlite'))
        assert (root/'spec/revision-proposals'/proposal['proposal_id']/'proposal.json').is_file()

def test_owner_review_reads_finished_checkpoint_but_cannot_write(store):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('pg-readonly-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2}
        saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        store.finish_worker(jid)
        review=GeneratorCheckpoint(store,cap,None,'spec',writable=False)
        assert review.get_tuple(cfg).checkpoint['channel_values']['count']==2
        with pytest.raises(PermissionError,match='read-only'):review.put(cfg,cp,{}, {})
        with pytest.raises(TenantAccessDenied,match='requires a claimed'):GeneratorCheckpoint(store,cap,None,'spec')

def test_checkpoint_stop_race_serializes_until_transaction_commits(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('pg-stop-race-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':1}
        saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
    holding=Event();release=Event();started=Event();done=Event()
    def writer():
        with saver._cursor() as c:
            assert c.execute('SELECT count(*) AS n FROM checkpoints WHERE thread_id=%s',(sid,)).fetchone()['n']==1
            holding.set();assert release.wait(5)
    def stopper():
        with tenant_context(A):
            started.set()
            with store._postgres.transaction() as c:c.execute('UPDATE tasks SET stop_requested_at=1 WHERE task_id=%s',(tid,))
            done.set()
    with ThreadPoolExecutor(2) as ex:
        writing=ex.submit(writer);assert holding.wait(5)
        stopping=ex.submit(stopper);assert started.wait(5)
        assert not done.wait(.1)
        release.set();writing.result(timeout=5);stopping.result(timeout=5)
    with tenant_context(A):
        with pytest.raises(TenantAccessDenied,match='authority lost'):saver.get_tuple(cfg)

def test_source_review_reads_pg_after_job_finishes_and_never_opens_sqlite(store,tmp_path,monkeypatch):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from delivery_api.revision_checkpoint_reader import read_source_checkpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('pg-source-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cfg={'configurable':{'thread_id':sid}}
        cp=empty_checkpoint();cp['channel_values']={'count':2};saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},{})
        store.finish_worker(jid)
        import sqlite3
        monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('SQLite forbidden')))
        assert read_source_checkpoint(store,cap,tmp_path/'absent/checkpoints.sqlite',session_id=sid,expected_checkpoint_id=cp['id'])=={'count':2}
        with pytest.raises(ValueError,match='changed'):read_source_checkpoint(store,cap,tmp_path/'absent/checkpoints.sqlite',session_id=sid,expected_checkpoint_id='stale')

def test_checkpoint_history_is_logical_and_namespace_bound(store):
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from langgraph.checkpoint.base import empty_checkpoint
    with tenant_context(A):
        tid=store.create_task('pg-history-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        saver=GeneratorCheckpoint(store,cap,jid,'private:history',logical_thread_id='future')
        cfg={'configurable':{'thread_id':'future'}}
        cp1=empty_checkpoint();cp1['channel_values']={'count':1};first=saver.put(cfg,cp1,{'source':'input','step':0,'parents':{}},{})
        cp2=empty_checkpoint();cp2['channel_values']={'count':2};second=saver.put(first,cp2,{'source':'loop','step':1,'parents':{}},{})
        rows=list(saver.list(cfg))
        assert [r.checkpoint['channel_values']['count'] for r in rows]==[2,1]
        assert all(r.config['configurable']['thread_id']=='future' and r.config['configurable']['checkpoint_ns']=='' for r in rows)
        assert rows[0].parent_config==first
        assert [r.config for r in saver.list(cfg,before=second)]==[first]
        with pytest.raises(TenantAccessDenied,match='namespace'):list(saver.list({'configurable':{'thread_id':'future','checkpoint_ns':'generator:spec'}}))
        with pytest.raises(ValueError):list(saver.list(None))
