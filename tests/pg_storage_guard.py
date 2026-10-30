"""Replacement for poisoning deleted adapter methods: prove native ownership."""
def assert_pg_storage_only(names=()):
    from delivery_api import store
    from delivery_api.postgres_store import PostgresStore
    assert not hasattr(store,'SQLiteStore')
    assert not hasattr(store,'TursoReplicaStore')
    assert PostgresStore.__bases__==(object,)
    for name in names:
        assert name in vars(PostgresStore),name
        assert callable(vars(PostgresStore)[name]),name
