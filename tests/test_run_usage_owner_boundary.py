"""A native PG run returns UUID owners; the usage endpoint consumes string IDs."""
import pytest
from starlette.requests import Request
from fastapi import HTTPException
from delivery_api.tenancy import tenant_context


@pytest.mark.parametrize('authenticated',[False,True])
def test_usage_normalizes_native_run_owner_before_auth_and_debit(pg_workspace_factory,monkeypatch,authenticated):
    from delivery_api import app as module,auth
    from uuid import UUID
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        run_id=store.create_link('Owner boundary')['run_id']
        assert isinstance(store.run(run_id)['owner_id'],UUID)
        monkeypatch.setattr(module,'store',store)
        monkeypatch.setattr(auth,'enabled',lambda:authenticated)
        monkeypatch.setattr(auth,'actor',lambda request,required=False:auth.Actor(workspace.scope.owner_id,'Owner',admin=False))
        # Legacy token quota provisioning is not the owner-boundary contract.
        monkeypatch.setattr(store,'quota_allows',lambda owner:(True,{'tokens_used':0,'token_quota':100}))
        request=Request({'type':'http','method':'POST','path':'/runs/'+run_id+'/usage','headers':[]})
        record=module.UsageRecord(record_json='{}',cost_usd=1.25,input_tokens=2,output_tokens=3)
        assert module.add_usage(run_id,record,request)=={'run_total_usd':1.25}
        assert store.run(run_id)['cost_usd']==1.25
        with workspace.admin() as connection:
            assert connection.execute('SELECT user_id::text,input_tokens,output_tokens FROM usage WHERE run_id=%s',(run_id,)).fetchone()==(workspace.scope.owner_id,2,3)
        if authenticated:
            monkeypatch.setattr(auth,'actor',lambda request,required=False:auth.Actor('00000000-0000-0000-0000-000000000099','Other',admin=False))
            with pytest.raises(HTTPException) as exc:
                module.add_usage(run_id,record,request)
            assert exc.value.status_code==403
            assert store.run(run_id)['cost_usd']==1.25
