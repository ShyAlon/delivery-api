"""Hermetic real graph/SQLite staged readback, no provider invocation."""
from pathlib import Path
import hashlib,json,sqlite3
from types import SimpleNamespace
import pytest
from delivery_api.tenancy import TenantScope,session_root
from delivery_api.revision_stage_evidence import read_validated_staged_receipt


def build_real_stage_fixture(tmp_path,*,cap=None,options_override=None,lineage_factory=None):
    pytest.importorskip('spec_generator.revision_checkpoint')
    from spec_generator.graph import build_graph,checkpoint_serde
    from spec_generator.revision_checkpoint import prepare_revision_seed,validate_revision_output,stage_reviewed_lineage_checkpoint
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.stop_fence import StopFence
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.base import empty_checkpoint
    from test_closed_revision_job import options
    source=json.loads((Path(__file__).parent/'fixtures'/'revision-source.json').read_text())
    scope=TenantScope('00000000-0000-0000-0000-000000000001')
    cap=cap or SimpleNamespace(scope=scope,task_id='task',session_id='s',generation=1,validate=lambda store:None)
    scope=cap.scope
    root=session_root(tmp_path,scope,cap.session_id);(root/'spec').mkdir(parents=True)
    conn=sqlite3.connect(root/'spec'/'checkpoints.sqlite');saver=SqliteSaver(conn,serde=checkpoint_serde())
    cp=empty_checkpoint();cp['channel_values']=source
    saved=saver.put({'configurable':{'thread_id':cap.session_id,'checkpoint_ns':''}},cp,{'source':'input','step':0,'parents':{}},{})
    conn.close();source_id=saved['configurable']['checkpoint_id']
    original=json.dumps(source['srs']).encode();(root/'spec'/'srs.json').write_bytes(original)
    o=options_override or options();o['source_checkpoint_id']=source_id;o['artifact_evidence'][0]['sha256']=hashlib.sha256(original).hexdigest()
    result=prepare_revision_seed(source=source,phase='srs',change_request=o['change_request'],session_id='s')['seed']
    result['srs']=source['srs'];result['gate_feedback'].pop('srs')
    digest=validate_revision_output(source=source,result=result,phase='srs',change_request=o['change_request'],session_id='s')['artifact_sha256']
    lineage={'kind':'closed_revision_execution_lineage','owner_id':scope.owner_id,'task_id':'task','semantic_session_id':'s',
        'parent_execution_session_id':'s','parent_generation':1,'execution_session_id':'next','generation':2,'phase':'srs',
        'source_checkpoint_id':source_id,'proposal_id':'a'*32,'regenerated_artifact_sha256':digest,'review_digest':o['preflight_digest'],
        'approved_source_artifact_evidence':o['artifact_evidence'],'state':'planned','installed':False,'dispatch_started':False}
    if lineage_factory:lineage=lineage_factory(o,source,result,digest)
    ld=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    dest=root/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id'];(dest/'spec').mkdir(parents=True);(dest/'interview').mkdir()
    conn=sqlite3.connect(dest/'spec'/'checkpoints.sqlite');graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=SqliteSaver(conn,serde=checkpoint_serde()))
    fence=StopFence(tmp_path/'fake-stop.json');fence.generation=2
    with cli_execution(fence,None):
        staged=stage_reviewed_lineage_checkpoint(source=source,proposal_result=result,lineage=lineage,change_request=o['change_request'],graph=graph,config={'configurable':{'thread_id':lineage['execution_session_id']}})
    state=graph.get_state({'configurable':{'thread_id':lineage['execution_session_id']}}).values;conn.close()
    documents={'interview/intent_model.json':source['intent_model'],'interview/conops.json':source.get('conops'),
        'spec/needs.json':source['needs'],'spec/system-baseline.json':source['system_baseline'],'spec/srs.json':state['srs']}
    for name,body in documents.items():
        (dest/name).write_text(json.dumps(body))
        if name!='spec/srs.json':
            (root/name).parent.mkdir(parents=True,exist_ok=True)
            (root/name).write_bytes((dest/name).read_bytes())
    files={name:hashlib.sha256((dest/name).read_bytes()).hexdigest() for name in list(documents)+['spec/checkpoints.sqlite']}
    manifest={'kind':'closed_revision_staged_manifest','lineage_sha256':ld,'checkpoint_id':staged['destination_checkpoint']['configurable']['checkpoint_id'],'files':files}
    (dest/'manifest.json').write_text(json.dumps(manifest));md=hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest()
    args=dict(store=None,capability=cap,artifacts_base=tmp_path,lineage=lineage,lineage_sha256=ld,manifest_sha256=md,options=o)
    return args,root,dest,documents,files,manifest,md,original,cap,lineage,ld


def test_real_staged_receipt_preserves_source_and_requires_fresh_gate(tmp_path):
    args,root,dest,documents,files,manifest,md,original,cap,lineage,ld=build_real_stage_fixture(tmp_path)
    assert read_validated_staged_receipt(**args)=={'manifest_sha256':md,'checkpoint_id':manifest['checkpoint_id']}
    assert (root/'spec'/'srs.json').read_bytes()==original
    from delivery_api.revision_source_review import read_source_review_snapshot
    review=read_source_review_snapshot(None,cap,artifacts_base=tmp_path,lineage=lineage,lineage_sha256=ld)
    assert not review['snapshot']['accepted'] and review['snapshot']['upstream_sha256']=={name:files[name] for name in documents if name!='spec/srs.json'}
    for key,value in (('installed',True),('dispatch_started',True),('state','installed'),('generation',True),('generation',3)):
        changed=lineage|{key:value};changed_digest=hashlib.sha256(json.dumps(changed,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with pytest.raises(ValueError,match='lineage differs|next generation'):
            read_source_review_snapshot(None,cap,artifacts_base=tmp_path,lineage=changed,lineage_sha256=changed_digest)
    # Re-hash a wrong JSON document: byte inventory is consistent but semantic
    # checkpoint evidence must still reject it.
    (dest/'interview'/'intent_model.json').write_text('{}')
    manifest['files']['interview/intent_model.json']=hashlib.sha256((dest/'interview'/'intent_model.json').read_bytes()).hexdigest()
    (dest/'manifest.json').write_text(json.dumps(manifest));args['manifest_sha256']=hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='upstream bytes differ|validated checkpoint channel'):read_validated_staged_receipt(**args)
