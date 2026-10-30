"""Remaining run/usage methods preserve tenant scope without SQLite parent SQL."""
import pytest
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from vmodel_contracts import RunState


def test_native_run_usage_artifacts_and_owner_isolation(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    from pg_storage_guard import assert_pg_storage_only
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store
        with tenant_context(a.scope):
            link=s.create_link('Native run');rid=link['run_id'];tid=s.create_task('Native artifact')['task_id']
        bridge=_Connection.execute
        def reject_family(self,sql,parameters=()):
            if not sql.startswith('\nSELECT t.*') and any(table in sql for table in (' runs',' links',' usage',' artifacts',' task_artifacts',' users')):
                raise AssertionError('run/usage family uses SQLite bridge')
            return bridge(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject_family)
            assert_pg_storage_only(('link', 'deactivate_link', 'set_state', 'run', 'set_submission', 'total_spend', 'add_artifact', 'artifacts', 'add_task_artifact', 'quota_allows', 'add_user_usage'))
            with tenant_context(a.scope):
                assert s.link(link['token'])['run_id']==rid
                s.set_submission(rid,'A request');s.set_state(rid,RunState.IN_INTERVIEW)
                assert s.run(rid)['submission']=='A request'
                assert s.add_user_usage(rid,a.scope.owner_id,'{}',1.25,3,4)==1.25
                assert s.total_spend()==1.25
                s.add_artifact(rid,'test','ref');assert s.artifacts(rid)[0]['ref']=='ref'
                s.add_task_artifact(tid,'intent','intent.json','ref')
                assert s.task_artifacts(tid)[0]['ref']=='ref'
                allowed,quota=s.quota_allows(a.scope.owner_id)
                assert isinstance(allowed,bool)
                s.deactivate_link(link['token']);assert s.link(link['token']) is None
            with tenant_context(b.scope):
                assert s.run(rid) is None
                assert s.artifacts(rid)==[]
                assert s.total_spend()==0
                with pytest.raises(TenantAccessDenied):
                    s.add_user_usage(rid,a.scope.owner_id,'{}',99,1,1)
