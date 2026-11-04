"""Fresh inputs come only from the current owned PG revision."""
import pytest
from uuid import uuid4
from delivery_api.producer_inputs import read_fresh_inputs,MAX_DOCUMENT_BYTES
from delivery_api.tenancy import session_root,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability


def setup(tmp_path,workspace,changes=None):
    store=workspace.store;tid=store.create_task('Fresh input durability')['task_id']
    store.set_engine_session(tid,'input-'+uuid4().hex);cap=WorkerCapability.capture(store,tid)
    documents={('intent','intent_model.json'):b'{"source":"intent"}',('conops','conops.json'):b'{"source":"conops"}',('baseline','system-baseline.json'):b'{"source":"baseline"}'}
    documents.update(changes or {})
    store.publish_task_revision_bytes(tid,[dict(phase_key=phase,name=name,data=data) for (phase,name),data in documents.items() if data is not None],writer_instance='fixture')
    return store,cap,session_root(tmp_path,cap.scope,cap.session_id)


def test_owned_revision_survives_absent_or_hostile_host_folder(tmp_path,pg_scoped_workspace):
    store,cap,root=setup(tmp_path,pg_scoped_workspace);before=store.manifest_snapshot(cap.task_id)
    expected={'intent':{'source':'intent'},'conops':{'source':'conops'},'baseline':{'source':'baseline'}}
    assert read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)==expected
    root.mkdir(parents=True);(root/'interview').symlink_to(tmp_path/'foreign')
    assert read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)==expected
    assert store.manifest_snapshot(cap.task_id)==before


@pytest.mark.parametrize('data',[None,b'[]',b'{broken',b' '*(MAX_DOCUMENT_BYTES+1)])
def test_missing_malformed_or_unbounded_pg_input_refused(tmp_path,pg_scoped_workspace,data):
    store,cap,root=setup(tmp_path,pg_scoped_workspace,{('intent','intent_model.json'):data})
    with pytest.raises(ValueError):read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)


def test_foreign_root_and_stale_generation_refused(tmp_path,pg_scoped_workspace):
    store,cap,root=setup(tmp_path,pg_scoped_workspace)
    with pytest.raises(TenantAccessDenied):read_fresh_inputs(store,cap,tmp_path,artifacts_base=tmp_path)
    with store._task_operation_guard('task.rerun'):
        store._execute_native('UPDATE tasks SET generation=generation+1 WHERE task_id=%s',(cap.task_id,),write=True)
    with pytest.raises(TenantAccessDenied):read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)
