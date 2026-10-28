"""Last-use proof for SQLite row/cursor/exception adapters in PG."""
from pathlib import Path
import pytest
from delivery_api.tenancy import tenant_context


def test_no_sqlite_row_cursor_or_error_conversion_remains():
    import delivery_api.postgres_store as module
    assert not hasattr(module,'_Row')
    assert not hasattr(module,'_Cursor')
    assert 'sqlite3' not in Path(module.__file__).read_text()
    assert not hasattr(module._Connection,'executemany')
    assert not hasattr(module._Connection,'commit')
    assert not hasattr(module._Connection,'rollback')


def test_native_create_collision_keeps_shape_and_transaction(pg_workspace_factory):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store;first=s.create_task('Native create')
        assert len(first['phases'])==8 and len(first['gates'])==8
        with pytest.raises(ValueError,match='already exists'):s.create_task('NATIVE CREATE')
        assert len(s.list_tasks())==1
        assert s.create_task('Next native')['title']=='Next native'


def test_production_has_no_lastrowid_or_db_transaction_shim_consumers():
    import ast
    root=Path(__file__).parents[1]/'src'/'delivery_api'
    violations=[]
    for path in root.rglob('*.py'):
        if path.name in ('store.py','libsql_store.py'):continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node,ast.Attribute):continue
            if node.attr=='lastrowid':violations.append((path.name,node.lineno,node.attr))
            if (node.attr in ('executemany','commit','rollback')
                and isinstance(node.value,ast.Attribute) and node.value.attr=='_db'):
                violations.append((path.name,node.lineno,node.attr))
    assert violations==[]
