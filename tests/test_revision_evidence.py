"""Phase evidence follows the owned PG revision, without consulting scratch."""
import hashlib
from uuid import uuid4
import pytest
from delivery_api.tenancy import session_root,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability
from delivery_api.revision_evidence import read_revision_artifacts
from delivery_api.producer_inputs import MAX_DOCUMENT_BYTES


def setup(tmp_path,workspace,data=b'{"exact":"reviewed"}\n'):
    store=workspace.store;tid=store.create_task('Revision evidence')['task_id']
    store.set_engine_session(tid,'evidence-'+uuid4().hex);cap=WorkerCapability.capture(store,tid)
    store.publish_task_revision_bytes(tid,[dict(phase_key='srs',name='srs.json',data=data)] if data is not None else [],writer_instance='fixture')
    return store,cap,session_root(tmp_path,cap.scope,cap.session_id),data


def test_reads_only_canonical_owned_bytes_without_mutation(tmp_path,pg_scoped_workspace):
    store,cap,root,data=setup(tmp_path,pg_scoped_workspace);before=store.manifest_snapshot(cap.task_id)
    expected=[{'phase':'srs','name':'srs.json','generation':cap.generation,'sha256':hashlib.sha256(data).hexdigest()}]
    assert read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)==expected
    root.mkdir(parents=True);(root/'spec').symlink_to(tmp_path/'foreign')
    assert read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)==expected
    assert store.manifest_snapshot(cap.task_id)==before


@pytest.mark.parametrize('data',[None,b' '*(MAX_DOCUMENT_BYTES+1)])
def test_missing_or_unbounded_evidence_refused(tmp_path,pg_scoped_workspace,data):
    store,cap,root,_=setup(tmp_path,pg_scoped_workspace,data)
    with pytest.raises(ValueError):read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)


def test_foreign_root_phase_owner_and_generation_refused(tmp_path,pg_scoped_workspace,pg_workspace_factory):
    store,cap,root,_=setup(tmp_path,pg_scoped_workspace)
    with pytest.raises(TenantAccessDenied):read_revision_artifacts(store,cap,tmp_path,'srs',artifacts_base=tmp_path)
    with pytest.raises(ValueError):read_revision_artifacts(store,cap,root,'needs',artifacts_base=tmp_path)
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as other,tenant_context(other.scope):
        with pytest.raises(TenantAccessDenied):read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)
    with store._task_operation_guard('task.rerun'):
        store._execute_native('UPDATE tasks SET generation=generation+1 WHERE task_id=%s',(cap.task_id,),write=True)
    with pytest.raises(TenantAccessDenied):read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)
