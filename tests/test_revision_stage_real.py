"""Hermetic real graph/SQLite staged readback, no provider invocation."""
from pathlib import Path
import hashlib,json,sqlite3
from types import SimpleNamespace
import pytest
from delivery_api.tenancy import TenantScope,session_root
from delivery_api.revision_stage_evidence import read_validated_staged_receipt


def build_real_stage_fixture(tmp_path,*,cap=None,options_override=None,lineage_factory=None,semantic_session_id='s',phase='srs'):
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
    conn=sqlite3.connect(root/'spec'/'checkpoints.sqlite');saver=SqliteSaver(conn,serde=checkpoint_serde())
    cp=empty_checkpoint();cp['channel_values']=source
    saved=saver.put({'configurable':{'thread_id':cap.session_id,'checkpoint_ns':''}},cp,{'source':'input','step':0,'parents':{}},{})
    conn.close();source_id=saved['configurable']['checkpoint_id']
    original=json.dumps(source[phase]).encode();(root/'spec'/(phase+'.json')).write_bytes(original)
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
    dest=root/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id'];(dest/'spec').mkdir(parents=True);(dest/'interview').mkdir()
    conn=sqlite3.connect(dest/'spec'/'checkpoints.sqlite');graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=SqliteSaver(conn,serde=checkpoint_serde()))
    fence=StopFence(tmp_path/'fake-stop.json');fence.generation=2
    with cli_execution(fence,None):
        staged=stage_reviewed_lineage_checkpoint(source=source,proposal_result=result,lineage=lineage,change_request=o['change_request'],graph=graph,config={'configurable':{'thread_id':lineage['execution_session_id']}})
    state=graph.get_state({'configurable':{'thread_id':lineage['execution_session_id']}}).values;conn.close()
    documents={'interview/intent_model.json':source['intent_model'],'interview/conops.json':source.get('conops'),
        'spec/needs.json':source['needs'],'spec/system-baseline.json':source['system_baseline'],'spec/'+phase+'.json':state[phase]}
    if phase=='architecture':documents['spec/srs.json']=source['srs']
    for name,body in documents.items():
        (dest/name).write_text(json.dumps(body))
        if name!='spec/'+phase+'.json':
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


def private_writer_fixture(tmp_path):
    import shutil
    from delivery_api.revision_stage_writer import prepare_private_revision_stage
    from delivery_api.revision_source_review import read_source_review_snapshot
    from spec_generator.stop_fence import StopFence
    from spec_generator.cooperative_run import cli_execution
    from test_revision_intent import proposal as proposal_fixture
    args,root,dest,documents,files,manifest,md,original,cap,lineage,ld=build_real_stage_fixture(tmp_path)
    # Test fixture builder output is removed only here to exercise a genuinely
    # fresh production writer destination, never runtime recovery behavior.
    shutil.rmtree(dest)
    proposal=proposal_fixture()|{'proposal_id':lineage['proposal_id'],'task_id':cap.task_id,'session_id':cap.session_id,
        'source_checkpoint_id':lineage['source_checkpoint_id'],'artifact':documents['spec/srs.json'],
        'artifact_sha256':lineage['regenerated_artifact_sha256']}
    raw=json.dumps(proposal,sort_keys=True,indent=2).encode()
    (dest.parent.parent/'proposal.json').write_bytes(raw)
    lineage=lineage|{'proposal_document_sha256':hashlib.sha256(raw).hexdigest()}
    ld=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    snapshot=read_source_review_snapshot(None,cap,artifacts_base=tmp_path,lineage=lineage,lineage_sha256=ld)
    before={name:(root/name).read_bytes() for name in list(documents)+['spec/checkpoints.sqlite']}
    control=StopFence(root/'interview'/'stop-request.json')
    kwargs=dict(artifacts_base=tmp_path,lineage=lineage,lineage_sha256=ld,options=args['options'],review_snapshot=snapshot)
    return before,root,dest,control,cap,kwargs,args


def test_private_stage_writer_preserves_exact_bytes_and_never_installs(tmp_path):
    from delivery_api.revision_stage_writer import prepare_private_revision_stage
    from spec_generator.cooperative_run import cli_execution
    before,root,dest,control,cap,kwargs,args=private_writer_fixture(tmp_path)
    snapshot=kwargs['review_snapshot'];lineage=kwargs['lineage'];ld=kwargs['lineage_sha256']
    with cli_execution(control,None):
        receipt=prepare_private_revision_stage(None,cap,**kwargs)
    assert not receipt['installed'] and not receipt['dispatch_started'] and not receipt['source_mutated']
    assert control.generation==1 and not (dest/'execution-generation.json').exists()
    assert not (dest/'interview'/'stop-request.json').exists()
    assert before=={name:(root/name).read_bytes() for name in before}
    for name in snapshot['snapshot']['upstream_sha256']:assert (dest/name).read_bytes()==before[name]
    args.update(lineage=lineage,lineage_sha256=ld,manifest_sha256=receipt['manifest_sha256'])
    assert read_validated_staged_receipt(**args)=={k:receipt[k] for k in ('manifest_sha256','checkpoint_id')}
    with cli_execution(control,None):
        with pytest.raises(ValueError,match='stage already exists'):
            prepare_private_revision_stage(None,cap,**kwargs)
    assert before=={name:(root/name).read_bytes() for name in before}


@pytest.mark.parametrize('change',['no_fence','foreign_fence','source_bytes','proposal_bytes','escape','symlink_stage'])
def test_private_writer_rejects_bad_basis_before_destination_write(tmp_path,change):
    from delivery_api.revision_stage_writer import prepare_private_revision_stage
    from spec_generator.stop_fence import StopFence
    from spec_generator.cooperative_run import cli_execution
    before,root,dest,control,cap,kwargs,args=private_writer_fixture(tmp_path)
    if change=='source_bytes':(root/'spec'/'needs.json').write_text(json.dumps(json.loads(before['spec/needs.json']),indent=2))
    if change=='proposal_bytes':(dest.parent.parent/'proposal.json').write_text('{}')
    if change=='escape':
        kwargs['lineage']=kwargs['lineage']|{'execution_session_id':'../escape'}
        kwargs['lineage_sha256']=hashlib.sha256(json.dumps(kwargs['lineage'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        kwargs['review_snapshot']['snapshot']['lineage_sha256']=kwargs['lineage_sha256']
        kwargs['review_snapshot']['snapshot_sha256']=hashlib.sha256(json.dumps(kwargs['review_snapshot']['snapshot'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if change=='symlink_stage':
        dest.parent.rmdir();outside=tmp_path/'outside';outside.mkdir();dest.parent.symlink_to(outside,target_is_directory=True)
    if change=='no_fence':
        with pytest.raises(ValueError):prepare_private_revision_stage(None,cap,**kwargs)
    else:
        if change=='foreign_fence':control=StopFence(tmp_path/'foreign-stop.json')
        with cli_execution(control,None):
            with pytest.raises((ValueError,OSError,PermissionError)):prepare_private_revision_stage(None,cap,**kwargs)
    assert not dest.exists()
    assert (root/'spec'/'checkpoints.sqlite').read_bytes()==before['spec/checkpoints.sqlite']


def test_private_writer_retains_partial_evidence_after_failure_no_auto_retry(tmp_path,monkeypatch):
    from delivery_api.revision_stage_writer import prepare_private_revision_stage
    from spec_generator.cooperative_run import cli_execution
    from spec_generator import revision_checkpoint
    before,root,dest,control,cap,kwargs,args=private_writer_fixture(tmp_path)
    def fail(**kw):raise ValueError('simulated staged checkpoint failure')
    monkeypatch.setattr(revision_checkpoint,'stage_private_lineage_checkpoint',fail)
    with cli_execution(control,None):
        with pytest.raises(ValueError,match='simulated'):prepare_private_revision_stage(None,cap,**kwargs)
        assert dest.exists() and (dest/'spec'/'checkpoints.sqlite').exists() and not (dest/'manifest.json').exists()
        with pytest.raises(ValueError,match='explicit reconciliation'):prepare_private_revision_stage(None,cap,**kwargs)
    assert before=={name:(root/name).read_bytes() for name in before}
