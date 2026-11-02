import pytest
from test_postgres_store import DSN
@pytest.fixture
def isolated_policy_database(monkeypatch):
    # Installer inspects global quiescence. A tenant fixture cannot isolate it
    # from other tests' deliberately unresolved accounting on the shared server.
    import psycopg,uuid
    from psycopg.conninfo import make_conninfo
    import pg_harness
    from delivery_api.postgres_store import PostgresStore
    name='test_tenant_policy_'+uuid.uuid4().hex[:12]
    admin_dsn=make_conninfo(DSN,dbname='postgres')
    isolated=make_conninfo(DSN,dbname=name)
    with psycopg.connect(admin_dsn,autocommit=True) as connection:
        connection.execute('CREATE DATABASE '+name)
    try:
        with psycopg.connect(isolated,autocommit=True) as connection:
            connection.execute(pg_harness.SUPABASE_STAND_IN)
            connection.execute(pg_harness.SUPABASE_DEFAULT_ACL)
            for path in sorted(pg_harness.MIGRATIONS.glob('*.sql')):
                if pg_harness._numbered(path):connection.execute(path.read_text())
            connection.execute(pg_harness.SEED)
        yield isolated
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as connection:
            connection.execute('DROP DATABASE '+name+' WITH (FORCE)')

