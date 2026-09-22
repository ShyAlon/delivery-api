import pytest
from vmodel_contracts.facets import COVERAGE_SCHEMA
from delivery_api.store import SQLiteStore


def test_task_schema_binding_is_pinned_and_idempotent():
    store=SQLiteStore(':memory:');task=store.create_task('Structured interview')
    expected={'task_id':task['task_id'],'schema_id':COVERAGE_SCHEMA.schema_id,'schema_version':COVERAGE_SCHEMA.schema_version}
    assert store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)==expected
    assert store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,COVERAGE_SCHEMA.schema_version)==expected
    assert store.interview_schema_binding(task['task_id'])==expected
    with pytest.raises(ValueError,match='immutable'):
        store.bind_interview_schema(task['task_id'],COVERAGE_SCHEMA.schema_id,'2.0.0')


def test_legacy_database_gets_additive_binding_columns(tmp_path):
    path=tmp_path/'delivery.db';store=SQLiteStore(str(path));task=store.create_task('Legacy');store._db.close()
    reopened=SQLiteStore(str(path));assert reopened.interview_schema_binding(task['task_id']) is None
