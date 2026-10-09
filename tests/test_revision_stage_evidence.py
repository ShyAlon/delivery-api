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
