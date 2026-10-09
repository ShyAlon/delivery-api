import hashlib,json
from types import SimpleNamespace
import pytest
from delivery_api.tenancy import TenantScope,session_root
from delivery_api.revision_stage_evidence import read_staged_revision_manifest


def setup(tmp_path):
    scope=TenantScope('00000000-0000-0000-0000-000000000001')
    cap=SimpleNamespace(scope=scope,task_id='task',session_id='parent',generation=1,validate=lambda store:None)
    lineage={'owner_id':scope.owner_id,'task_id':'task','parent_execution_session_id':'parent','parent_generation':1,
        'generation':2,'state':'planned','installed':False,'dispatch_started':False,'proposal_id':'a'*32,'execution_session_id':'next','semantic_session_id':'semantic','phase':'srs'}
    digest=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    root=session_root(tmp_path,scope,'parent')/'spec'/'revision-proposals'/('a'*32)/'staged'/'next'
    files={}
    for name in ('interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json','spec/checkpoints.sqlite','spec/srs.json'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture bytes')
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'kind':'closed_revision_staged_manifest','lineage_sha256':digest,'checkpoint_id':'checkpoint','files':files}
    path=root/'manifest.json';path.write_text(json.dumps(manifest));md=hashlib.sha256(path.read_bytes()).hexdigest()
    return root,dict(store=None,capability=cap,artifacts_base=tmp_path,lineage=lineage,lineage_sha256=digest,manifest_sha256=md)


def test_exact_manifest_receipt_never_installs(tmp_path):
    root,args=setup(tmp_path)
    assert read_staged_revision_manifest(**args)=={'manifest_sha256':args['manifest_sha256'],'checkpoint_id':'checkpoint'}
    assert not (root/'execution-generation.json').exists()


@pytest.mark.parametrize('unsafe',['file-drift','missing','symlink','manifest-drift','lineage-drift'])
def test_staged_readback_rejects_changed_or_unsafe_evidence(tmp_path,unsafe):
    root,args=setup(tmp_path);path=root/'spec'/'srs.json'
    if unsafe=='file-drift':path.write_text('changed')
    elif unsafe=='missing':path.unlink()
    elif unsafe=='symlink':path.unlink();path.symlink_to(tmp_path/'outside')
    elif unsafe=='manifest-drift':(root/'manifest.json').write_text('{}')
    else:args['lineage']['task_id']='foreign'
    with pytest.raises((OSError,ValueError,PermissionError)):read_staged_revision_manifest(**args)


def test_active_checkpoint_sidecar_is_rejected(tmp_path):
    root,args=setup(tmp_path);(root/'spec'/'checkpoints.sqlite-wal').write_bytes(b'pending')
    with pytest.raises(ValueError,match='sidecar-free'):read_staged_revision_manifest(**args)


def test_directory_swap_during_read_is_rejected(tmp_path,monkeypatch):
    import os
    from delivery_api import revision_stage_evidence as module
    root,args=setup(tmp_path);original=os.open;changed=[]
    def opened(name,flags,*values,**kwargs):
        fd=original(name,flags,*values,**kwargs)
        if name=='manifest.json' and not changed:
            root.rename(root.with_name('old'));root.mkdir();changed.append(True)
        return fd
    monkeypatch.setattr(module.os,'open',opened)
    with pytest.raises((ValueError,PermissionError,OSError)):read_staged_revision_manifest(**args)


def test_validated_receipt_connects_pinned_checkpoint_and_source_evidence(tmp_path,monkeypatch):
    import sys
    from types import ModuleType
    from delivery_api import revision_stage_evidence as module,revision_evidence
    from test_closed_revision_job import options
    root,args=setup(tmp_path);o=options();l=args['lineage']
    l.update(source_checkpoint_id=o['source_checkpoint_id'],review_digest=o['preflight_digest'],approved_source_artifact_evidence=o['artifact_evidence'])
    args['lineage_sha256']=hashlib.sha256(json.dumps(l,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    calls=[];reads=[]
    source=ModuleType('spec_generator.revision_source');checkpoint=ModuleType('spec_generator.revision_checkpoint')
    def read(path,**kwargs):
        reads.append(kwargs)
        return {'state':'staged'} if kwargs.get('expected_review_phase') else {'state':'source'}
    source.read_closed_checkpoint=read
    checkpoint.validate_staged_lineage_state=lambda **kwargs:calls.append(kwargs)
    monkeypatch.setitem(sys.modules,'spec_generator.revision_source',source);monkeypatch.setitem(sys.modules,'spec_generator.revision_checkpoint',checkpoint)
    monkeypatch.setattr(module,'read_approved_upstream_bytes',lambda *a,**kwargs:{})
    monkeypatch.setattr(module,'read_staged_revision_manifest',lambda **kwargs:{'manifest_sha256':args['manifest_sha256'],'checkpoint_id':'staged-checkpoint'})
    monkeypatch.setattr(revision_evidence,'read_revision_artifacts',lambda *a,**kwargs:o['artifact_evidence'])
    result=module.read_validated_staged_receipt(**args,options=o)
    assert result['checkpoint_id']=='staged-checkpoint' and len(reads)==3 and calls[0]['change_request']==o['change_request']
    assert reads[1]['expected_review_phase']=='srs' and reads[1]['session_id']=='next'
    calls.clear();monkeypatch.setattr(revision_evidence,'read_revision_artifacts',lambda *a,**kwargs:[])
    with pytest.raises(ValueError,match='source artifacts changed'):module.read_validated_staged_receipt(**args,options=o)


def test_valid_hashes_cannot_hide_document_checkpoint_mismatch(tmp_path):
    root,args=setup(tmp_path)
    expected={name:{} for name in ('interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json','spec/srs.json')}
    for name in expected:(root/name).write_text('{}')
    (root/'spec'/'srs.json').write_text('{"changed":true}')
    manifest=json.loads((root/'manifest.json').read_text())
    for name in manifest['files']:manifest['files'][name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest));args['manifest_sha256']=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='validated checkpoint channel'):
        read_staged_revision_manifest(**args,expected_documents=expected)


def test_approved_upstream_reader_retains_exact_bytes_and_rejects_checkpoint_drift(tmp_path):
    from delivery_api.revision_stage_evidence import read_approved_upstream_bytes
    (tmp_path/'interview').mkdir();path=tmp_path/'interview'/'intent_model.json'
    path.write_text('{  "approved": true }\n');raw=path.read_bytes()
    expected={'interview/intent_model.json':{'approved':True}}
    assert read_approved_upstream_bytes(tmp_path,expected_documents=expected)=={'interview/intent_model.json':hashlib.sha256(raw).hexdigest()}
    assert path.read_bytes()==raw
    with pytest.raises(ValueError,match='checkpoint channel'):
        read_approved_upstream_bytes(tmp_path,expected_documents={'interview/intent_model.json':{}})


def test_equal_json_different_staged_bytes_are_rejected(tmp_path):
    root,args=setup(tmp_path);path=root/'interview'/'intent_model.json';path.write_text('{"same":true}')
    manifest=json.loads((root/'manifest.json').read_text());manifest['files']['interview/intent_model.json']=hashlib.sha256(path.read_bytes()).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest));args['manifest_sha256']=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    original=b'{ "same": true }\n'
    with pytest.raises(ValueError,match='upstream bytes differ'):
        read_staged_revision_manifest(**args,expected_upstream_sha256={'interview/intent_model.json':hashlib.sha256(original).hexdigest()})
