"""Task projection ports must run without the SQLite query/lock compatibility path."""
import pytest
from delivery_api.tenancy import tenant_context


def test_native_snapshot_rename_and_artifact_reads_do_not_use_bridge(pg_workspace_factory, monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
        store = workspace.store
        first = store.create_task('Native task')
        second = store.create_task('Other task')
        store.add_event(first['task_id'],'test','Kept event',payload={'kept':True})
        original = _Connection.execute
        def reject_legacy(self, sql, parameters=()):
            if sql.startswith(('UPDATE tasks SET title=', 'SELECT * FROM task_artifacts')) or 'AS events_json' in sql:
                raise AssertionError('task family still uses SQLite SQL bridge')
            return original(self,sql,parameters)
        monkeypatch.setattr(_Connection,'execute',reject_legacy)
        from delivery_api.store import SQLiteStore
        def parent_forbidden(*args, **kwargs):
            raise AssertionError('PG task family still delegates to SQLite parent')
        for name in ('task_snapshot','rename_task','task_artifacts','_task_record','_gate_record','_event_record'):
            monkeypatch.setattr(SQLiteStore,name,parent_forbidden)
        snapshot = store.task_snapshot(first['task_id'])
        assert snapshot['task']['title'] == 'Native task'
        assert snapshot['events'][-1]['payload'] == {'kept':True}
        assert len(snapshot['task']['gates']) == 8
        assert isinstance(snapshot['task']['gates'][0]['options'],list)
        assert store.task_snapshot('absent') is None
        assert store.task_artifacts(first['task_id']) == []
        assert store.task_artifacts(first['task_id'],include_stale=False) == []
        assert store.rename_task(first['task_id'],'  Renamed native  ')['title'] == 'Renamed native'
        with pytest.raises(ValueError,match='already exists'):
            store.rename_task(first['task_id'],second['title'].upper())
        assert store.task(first['task_id'])['title'] == 'Renamed native'
        with pytest.raises(ValueError,match='empty'):
            store.rename_task(first['task_id'],'   ')
        with pytest.raises(KeyError):
            store.rename_task('absent','Missing')
        assert any(e['action']=='task.renamed' and e['detail']=={'title':'Renamed native'} for e in store.audit_entries(first['task_id']))


def test_native_rename_audit_failure_rolls_back_title(pg_workspace_factory, monkeypatch):
    with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
        store = workspace.store
        task_id = store.create_task('Before atomic rename')['task_id']
        original = store.audit
        def fail_audit(actor, action, *args, **kwargs):
            if action == 'task.renamed':
                raise RuntimeError('audit unavailable')
            return original(actor,action,*args,**kwargs)
        monkeypatch.setattr(store,'audit',fail_audit)
        with pytest.raises(RuntimeError, match='audit unavailable'):
            store.rename_task(task_id,'Must roll back')
        assert store.task(task_id)['title'] == 'Before atomic rename'
