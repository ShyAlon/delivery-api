"""Real producer integration must not open a SQLite receiver."""
import ast
from pathlib import Path


def test_package_integration_uses_scoped_pg_and_no_private_sqlite_update():
    source=(Path(__file__).parent/'test_package_integration.py').read_text()
    tree=ast.parse(source)
    case=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='test_package_endpoint_calls_real_zero_model_export_and_downloads')
    assert 'pg_scoped_workspace' in [a.arg for a in case.args.args]
    assert 'SQLiteStore' not in source
    assert 'store._db' not in source
