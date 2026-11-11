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
