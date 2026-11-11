from types import SimpleNamespace
import pytest
from delivery_api.tenancy import tenant_context,TenantScope,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability

def test_frozen_answer_authority_reused_only_in_scope_and_final_is_fresh():
    from delivery_api.worker_capability import answer_authority
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
    cap=WorkerCapability(scope,'t','s',1)
    calls=[]
    class Store:
        def authority_snapshot(self,*a):calls.append(a);return {'generation':1},{'task_id':'t','generation':1},True
    store=Store()
    with tenant_context(scope):
        task=cap.validate(store)
        with answer_authority(store,cap,task):
            cap.validate(store)['generation']=99
            assert cap.validate(store)['generation']==1
            with tenant_context(TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2')):
                with pytest.raises(TenantAccessDenied):cap.validate(store)
        cap.validate(store)
    assert len(calls)==2


def test_frozen_input_verifies_retained_digest_without_owner_reread():
    from delivery_api.child_operation_refs import ChildOperationRefs
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
    class Store:
        def task(self,tid):return {'owner_id':scope.owner_id}
        def retain_decision_review(self,*a):pass
    with tenant_context(scope):
        refs=ChildOperationRefs(Store(),'t')
        value={'answer':[1]};ref=refs.put('answer',value)
        got=refs.frozen(ref,'answer',value);got['answer'].append(2)
        assert value=={'answer':[1]}
        with pytest.raises(PermissionError):refs.frozen(ref,'answer',{'answer':[2]})
        with tenant_context(TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2')),pytest.raises(PermissionError):refs.frozen(ref,'answer',value)


def test_answer_watch_reuses_verified_entry_but_catches_intervening_wake(monkeypatch):
    import asyncio,threading
    from delivery_api import stop_watch
    calls=[];stop=threading.Event()
    class Store:
        def stop_state(self,*a):calls.append(a);return {'task_present':True,'stop_requested_at':1,'active':True,'session_generation':1}
    def wait(key,seconds,gone,version):
        assert version==7
        return 8  # A Stop committed after the entry verified version 7.
    monkeypatch.setattr(stop_watch.event_bus,'wait',wait)
    asyncio.run(stop_watch.watch_for_stop(store=Store(),task_id='t',session_id='s',generation=1,stop=stop,entry_version=7))
    assert stop.is_set() and len(calls)==1
