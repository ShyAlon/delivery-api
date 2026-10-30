"""PG private stage is immutable evidence, never an installed session."""
import hashlib,json,uuid
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context,TenantAccessDenied


def test_pg_private_stage_preserves_fresh_gate_and_source_without_sqlite(store,tmp_path,monkeypatch):
    from pathlib import Path
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    from delivery_api.pg_revision_stage import retain_stage,read_stage
    from langgraph.checkpoint.base import empty_checkpoint
    from spec_generator.revision_checkpoint import prepare_revision_seed
    from spec_generator.stop_fence import StopFence
    from spec_generator.cooperative_run import cli_execution
    source=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())
    with tenant_context(A):
        tid=store.create_task('pg-stage-'+uuid.uuid4().hex)['task_id'];sid='gen-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);jid=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(jid)
        for key in ('intent_model','conops','needs','srs'):source[key]['session_id']=sid
        source['srs']['intent_model_ref']=sid
        saver=GeneratorCheckpoint(store,cap,jid,'spec');cp=empty_checkpoint();cp['channel_values']=deepcopy(source);cp['channel_versions']={k:1 for k in source}
        cfg={'configurable':{'thread_id':sid}};saver.put(cfg,cp,{'source':'input','step':0,'parents':{}},cp['channel_versions'])
        result=prepare_revision_seed(source=source,phase='srs',change_request='Describe offline behavior',session_id=sid)['seed']
        result['srs']=deepcopy(source['srs']);result['gate_feedback'].pop('srs')
        lineage={'kind':'closed_revision_execution_lineage','owner_id':A.owner_id,'task_id':tid,'semantic_session_id':sid,
            'parent_execution_session_id':sid,'parent_generation':1,'execution_session_id':'next-'+uuid.uuid4().hex,'generation':2,
            'phase':'srs','source_checkpoint_id':cp['id'],'proposal_id':uuid.uuid4().hex,'state':'planned','installed':False,'dispatch_started':False}
        lineage['regenerated_artifact_sha256']=hashlib.sha256(json.dumps(result['srs'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        digest=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        import sqlite3
        monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('SQLite forbidden')))
        fence=StopFence(tmp_path/'tenants'/A.owner_id/'sessions'/sid/'interview/stop-request.json')
        with cli_execution(fence,None):
            receipt=retain_stage(store,cap,source=source,result=result,lineage=lineage,lineage_sha256=digest,change_request='Describe offline behavior',validate_authority=lambda:cap.validate(store))
        store.finish_worker(jid)
        stage=read_stage(store,cap,receipt,lineage=lineage,lineage_sha256=digest)
        assert stage['state'].next==('gate_srs',)
        assert stage['state'].values['srs']==source['srs']
        assert stage['state'].config['configurable']['thread_id']==lineage['execution_session_id']
        assert GeneratorCheckpoint(store,cap,None,'spec',writable=False).get_tuple(cfg).checkpoint['channel_values']==source
        assert store.session_binding(lineage['execution_session_id']) is None
        with pytest.raises(ValueError,match='digest'):read_stage(store,cap,receipt|{'manifest_sha256':'0'*64},lineage=lineage,lineage_sha256=digest)
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):read_stage(store,cap,receipt,lineage=lineage,lineage_sha256=digest)


def test_stage_refuses_wrong_lineage_before_private_effects():
    from delivery_api.pg_revision_stage import validate_lineage,digest
    cap=SimpleNamespace(scope=A,task_id='task',session_id='parent',generation=1)
    body={'kind':'closed_revision_execution_lineage','owner_id':A.owner_id,'task_id':'other','parent_execution_session_id':'parent','parent_generation':1,
        'generation':2,'state':'planned','installed':False,'dispatch_started':False}
    with pytest.raises(ValueError,match='captured owner'):validate_lineage(cap,body,digest(body))
