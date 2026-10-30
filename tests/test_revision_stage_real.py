"""Hermetic real graph/SQLite staged readback, no provider invocation."""
from pathlib import Path
import hashlib,json
from types import SimpleNamespace
import pytest
pytestmark=pytest.mark.producer_integration
from delivery_api.tenancy import TenantScope,session_root
from delivery_api.revision_stage_evidence import read_validated_staged_receipt


def build_real_stage_fixture(tmp_path,*,cap=None,options_override=None,lineage_factory=None,semantic_session_id='s',phase='srs',pg_store=None):
    pytest.importorskip('spec_generator.revision_checkpoint')
    from spec_generator.graph import build_graph,checkpoint_serde
    from spec_generator.revision_checkpoint import prepare_revision_seed,validate_revision_output,stage_reviewed_lineage_checkpoint
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.stop_fence import StopFence
    from langgraph.checkpoint.base import empty_checkpoint
    from test_closed_revision_job import options
    source=json.loads((Path(__file__).parent/'fixtures'/'revision-source.json').read_text())
    scope=TenantScope('00000000-0000-0000-0000-000000000001')
    cap=cap or SimpleNamespace(scope=scope,task_id='task',session_id='s',generation=1,validate=lambda store:None)
    scope=cap.scope
    if semantic_session_id!='s':
        for key in ('intent_model','conops','needs','srs'):
            source[key]['session_id']=semantic_session_id
        source['srs']['intent_model_ref']=semantic_session_id
    if phase=='architecture':
        from vmodel_contracts.architecture import ArchitectureOverview
        source['architecture']=ArchitectureOverview.model_validate({'session_id':semantic_session_id,'baseline_id':source['system_baseline']['baseline_id'],
            'stack_entries':[],'components':[{'name':'api','responsibility':'serve','requirement_ids':['R-1'],'baseline_refs':['repo:spec-generator']}],
            'decisions':[],'non_functionals':{}}).model_dump(mode='json')
    root=session_root(tmp_path,scope,cap.session_id);(root/'spec').mkdir(parents=True)
    cp=empty_checkpoint();cp['channel_values']=source
    if pg_store is None:raise ValueError('real PG fixture required')
    # New PG fixture, not a runtime import or historical migration.
    from delivery_api.generator_checkpoint import GeneratorCheckpoint
    jid=pg_store.enqueue_worker(cap.task_id,'phase:'+phase);claimed=pg_store.claim_worker(jid)
    saver=GeneratorCheckpoint(pg_store,claimed,jid,'spec')
    cp['channel_versions']={key:1 for key in source}
    saved=saver.put({'configurable':{'thread_id':cap.session_id}},cp,{'source':'input','step':0,'parents':{}},cp['channel_versions'])
    pg_store.finish_worker(jid)
    source_id=saved['configurable']['checkpoint_id']
    original=json.dumps(source[phase]).encode();(root/'spec'/(phase+'.json')).write_bytes(original)
    if pg_store is not None:
        artifacts=[{'phase_key':phase,'name':phase+'.json','data':original}]
        for artifact_phase,name,key in [('intent','intent_model.json','intent_model'),('conops','conops.json','conops'),('needs','needs.json','needs'),('baseline','system-baseline.json','system_baseline')]:
            artifacts.append({'phase_key':artifact_phase,'name':name,'data':json.dumps(source[key]).encode()})
        if phase=='architecture':artifacts.append({'phase_key':'srs','name':'srs.json','data':json.dumps(source['srs']).encode()})
        pg_store.publish_task_revision_bytes(cap.task_id,artifacts,writer_instance='constructed-test-only')
    o=options_override or options();o['source_checkpoint_id']=source_id;o['artifact_evidence'][0]['sha256']=hashlib.sha256(original).hexdigest()
    result=prepare_revision_seed(source=source,phase=phase,change_request=o['change_request'],session_id=semantic_session_id)['seed']
    result[phase]=source[phase];result['gate_feedback'].pop(phase)
    digest=validate_revision_output(source=source,result=result,phase=phase,change_request=o['change_request'],session_id=semantic_session_id)['artifact_sha256']
    lineage={'kind':'closed_revision_execution_lineage','owner_id':scope.owner_id,'task_id':'task','semantic_session_id':'s',
        'parent_execution_session_id':'s','parent_generation':1,'execution_session_id':'next','generation':2,'phase':phase,
        'source_checkpoint_id':source_id,'proposal_id':'a'*32,'regenerated_artifact_sha256':digest,'review_digest':o['preflight_digest'],
        'approved_source_artifact_evidence':o['artifact_evidence'],'state':'planned','installed':False,'dispatch_started':False}
    if lineage_factory:lineage=lineage_factory(o,source,result,digest)
    ld=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    dest=root/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id']
    if pg_store is not None:
        # Construct only non-durable graph state. Real stage publication below is
        # the production PG writer under test, never a file checkpoint import.
        from langgraph.checkpoint.memory import MemorySaver
        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=MemorySaver(serde=checkpoint_serde()))
        fence=StopFence(tmp_path/'fake-stop.json');fence.generation=2
        with cli_execution(fence,None):
            staged=stage_reviewed_lineage_checkpoint(source=source,proposal_result=result,lineage=lineage,change_request=o['change_request'],graph=graph,config={'configurable':{'thread_id':lineage['execution_session_id']}})
        state=graph.get_state({'configurable':{'thread_id':lineage['execution_session_id']}}).values
        documents={'interview/intent_model.json':source['intent_model'],'interview/conops.json':source.get('conops'),
            'spec/needs.json':source['needs'],'spec/system-baseline.json':source['system_baseline'],'spec/'+phase+'.json':state[phase]}
        if phase=='architecture':documents['spec/srs.json']=source['srs']
        files={name:hashlib.sha256(json.dumps(body).encode()).hexdigest() for name,body in documents.items()}
        manifest={'checkpoint_id':staged['destination_checkpoint']['configurable']['checkpoint_id']}
        args=dict(store=pg_store,capability=cap,artifacts_base=tmp_path,lineage=lineage,lineage_sha256=ld,manifest_sha256=None,options=o)
        return args,root,dest,documents,files,manifest,None,original,cap,lineage,ld
