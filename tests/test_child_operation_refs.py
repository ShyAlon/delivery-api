import pytest,uuid
from delivery_api.child_operation_refs import ChildOperationRefs
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B


def test_durable_immutable_owner_scoped_refs(store):
    with tenant_context(A):
        task=store.create_task('child refs '+uuid.uuid4().hex);tid=task['task_id']
        refs=ChildOperationRefs(store,tid)
        ref=refs.put('start',{'idea':'private'})
        assert ChildOperationRefs(store,tid).get(ref,'start')=={'idea':'private'}
        assert ref==refs.put('start',{'idea':'private'})
        with pytest.raises(PermissionError):refs.get(ref,'answer')
    with tenant_context(B),pytest.raises(PermissionError):ChildOperationRefs(store,tid).get(ref,'start')
    with tenant_context(A):store.delete_task(tid)
