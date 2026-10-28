import pytest
from vmodel_contracts.facets import COVERAGE_SCHEMA
from delivery_api.store import SQLiteStore


def test_task_schema_binding_is_pinned_and_idempotent(pg_scoped_workspace):
    store=pg_scoped_workspace.store;task=store.create_task('Structured interview')
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    expected={'task_id':task['task_id'],'schema_id':COVERAGE_SCHEMA.schema_id,'schema_version':COVERAGE_SCHEMA.schema_version}
    assert store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)==expected
    assert store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)==expected
    assert store.interview_schema_binding(task['task_id'])==expected
    with pytest.raises(ValueError,match='immutable'):
        store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,'2.0.0')

    # Reconstructing an independent adapter must read the same pinned identity.
    reopened=PostgresStore(store._postgres.dsn)
    assert reopened.interview_schema_binding(task['task_id'])==expected


def test_schema_binding_rejects_foreign_owner_and_missing_task(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as owner,pg_workspace_factory() as foreign:
        with tenant_context(owner.scope):
            tid=owner.store.create_task('Private binding')['task_id']
            expected=owner.store.bind_interview_schema(tid,COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)
            with pytest.raises(KeyError):owner.store.bind_interview_schema('missing',COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)
        with tenant_context(foreign.scope):
            with pytest.raises(KeyError):foreign.store.interview_schema_binding(tid)
            with pytest.raises(KeyError):foreign.store.bind_interview_schema(tid,COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)
        with tenant_context(owner.scope):assert owner.store.interview_schema_binding(tid)==expected


def test_legacy_database_gets_additive_binding_columns(tmp_path):
    path=tmp_path/'delivery.db';store=SQLiteStore(str(path));task=store.create_task('Legacy');store._db.close()
    reopened=SQLiteStore(str(path));assert reopened.interview_schema_binding(task['task_id']) is None
