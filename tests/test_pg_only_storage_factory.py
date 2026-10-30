"""No local persistence adapter remains behind the PG factory."""
import ast
from pathlib import Path
import pytest


def test_store_module_has_only_protocol_phase_contract_and_pg_factory():
    from delivery_api import store
    tree=ast.parse(Path(store.__file__).read_text())
    assert not hasattr(store,'SQLiteStore') and not hasattr(store,'TursoReplicaStore')
    assert not hasattr(store,'SCHEMA')
    assert {node.name for node in tree.body if isinstance(node,ast.ClassDef)}=={'Storage'}
    assert not any(isinstance(node,ast.Import) and any(name.name=='sqlite3' for name in node.names) for node in tree.body)

@pytest.mark.parametrize('dsn',[':memory:','/tmp/old.db','sqlite:////tmp/old.db','libsql://old',''])
def test_factory_rejects_non_pg_before_any_connection(dsn):
    from delivery_api.store import create_store
    with pytest.raises(ValueError,match='PostgreSQL'):create_store(dsn)


def test_unused_replica_module_and_local_database_config_are_absent():
    import importlib.util
    from delivery_api import config
    assert importlib.util.find_spec('delivery_api.libsql_store') is None
    for name in ('local_database_path','DEFAULT_DB_PATH','TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','TURSO_SYNC_INTERVAL'):
        assert not hasattr(config,name)
