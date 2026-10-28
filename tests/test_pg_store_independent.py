"""Consumer proof for removing the PG store's SQLite base and union wrapper."""
import ast
from pathlib import Path
import pytest


def test_pg_store_has_no_sqlite_parent_and_explicit_operation_coverage():
    from delivery_api.postgres_store import PostgresStore,PG_OPERATIONS,_LOCAL_AUTH
    from delivery_api.store import SQLiteStore,Storage
    assert SQLiteStore not in PostgresStore.__mro__
    public={name for name,value in vars(PostgresStore).items() if not name.startswith('_') and callable(value)}
    assert public==set(PG_OPERATIONS)|_LOCAL_AUTH
    assert len(PG_OPERATIONS)==len(set(PG_OPERATIONS))
    assert all(hasattr(getattr(PostgresStore,name),'__wrapped__') for name in public)
    # Structural API consumers keep local-auth methods as explicit refusals.
    required={name for name,value in vars(Storage).items() if not name.startswith('_') and callable(value)}
    assert required<=public


def test_every_self_dependency_is_owned_not_inherited():
    source=Path(__file__).parents[1]/'src/delivery_api/postgres_store.py'
    tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PostgresStore')
    definitions={n.name for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    definitions|={target.id for n in cls.body if isinstance(n,ast.Assign) for target in n.targets if isinstance(target,ast.Name)}
    assigned={n.attr for n in ast.walk(cls) if isinstance(n,ast.Attribute) and isinstance(n.ctx,ast.Store) and isinstance(n.value,ast.Name) and n.value.id=='self'}
    used={n.attr for n in ast.walk(cls) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='self'}
    assert used-definitions-assigned==set()
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='super' for n in ast.walk(cls))


def test_explicit_auth_refusals_still_fail_closed(pg_workspace_factory):
    from delivery_api.postgres_store import _LOCAL_AUTH
    from delivery_api.tenancy import tenant_context,TenantAccessDenied
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        for name in _LOCAL_AUTH:
            with pytest.raises(TenantAccessDenied):getattr(workspace.store,name)()
