"""Default imports use owned disposable PG, never caller credentials.

Reference adapters remain explicit test fixtures until separately retired.
"""
import os
import tempfile

_scratch = tempfile.mkdtemp(prefix="delivery-api-tests-")
for _name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ[_name] = ""
os.environ["TURSO_SYNC_INTERVAL"] = "60"
os.environ["XDG_DATA_HOME"] = _scratch
os.environ.pop("DELIVERY_DATABASE_URL",None)

# Private item6 scripts are test-only modules, not installed application imports.
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/item6'))

# A throwaway real Postgres for the database tests (see tests/pg_harness.py). Off with DELIVERY_TEST_NO_POSTGRES=1.
import pg_harness
os.environ.pop("DELIVERY_TEST_POSTGRES_DSN",None)
pg_harness.start()
if pg_harness.STATE['status']!='running':
    raise RuntimeError('test app requires its own disposable PG server: '+str(pg_harness.STATE))
from psycopg.conninfo import conninfo_to_dict
from urllib.parse import quote,urlencode
_pg_fields=conninfo_to_dict(pg_harness.STATE['dsn'])
_pg_database=_pg_fields.pop('dbname');_pg_fields['user']='delivery_app'
os.environ['DELIVERY_DATABASE_URL']='postgresql:///'+quote(_pg_database,safe='')+'?'+urlencode(_pg_fields)
os.environ['DELIVERY_SUPABASE_URL']='https://fixture.supabase.co'
os.environ['DELIVERY_SUPABASE_ANON_KEY']='hermetic-public-key'
os.environ['DELIVERY_CLOUD_EXECUTION']='0'

# Files that use the `account` or `ledger` fixtures get them from here, not only from the files that define them.
from test_attempt_ledger import ledger  # noqa: E402,F401


import pytest


@pytest.fixture
def store():
    """The app-role store on the test database. Shared here because several files use the `ledger` fixture, which needs it."""
    dsn=os.environ.get('DELIVERY_TEST_POSTGRES_DSN')
    if not dsn:pytest.skip('requires isolated migrated Postgres')
    from delivery_api.postgres_store import PostgresStore
    return PostgresStore(dsn.replace('user=sandbox','user=delivery_app'))

@pytest.fixture(autouse=True)
def _no_compute_segments_left_for_the_test_owners():
    """Compute segments are owner-wide and block admission while open, so a test that dispatches a phase must not leave one for the next test."""
    yield
    dsn=os.environ.get('DELIVERY_TEST_POSTGRES_DSN')
    if not dsn:return
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM retail_compute_segments WHERE owner_id IN (SELECT id FROM auth.users)")
    except Exception:pass

# Additive SQLite-retirement phase 1: isolated tenants on the session PG server.
from pg_workspace import pg_scoped_workspace, pg_workspace_factory, pg_task_app  # noqa: E402,F401
from pg_workspace import pg_browser  # noqa: E402,F401

@pytest.fixture(params=['sqlite','postgres'])
def evidence_store(request, tmp_path, pg_workspace_factory):
    """One behavioral contract, SQLite reference and the restricted PG path."""
    if request.param == 'sqlite':
        from delivery_api.store import SQLiteStore
        store = SQLiteStore(str(tmp_path/'evidence.db'))
        try:
            yield store
        finally:
            store._db.close()
    else:
        from delivery_api.tenancy import tenant_context
        with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
            yield workspace.store

from pg_bridge_guard import pg_bridge_poison  # noqa: E402,F401
from pg_runtime import pg_runtime_environment, pg_runtime_process  # noqa: E402,F401
from pg_local_compat import pg_local_compat_process  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _default_app_uses_hermetic_jwks(monkeypatch):
    """Keep signature/session checks real; replace only remote JWKS transport."""
    from delivery_api import app as module
    from delivery_api.tenant_http import TenantMiddleware
    from pg_workspace import Keys
    for middleware in module.app.user_middleware:
        if middleware.cls is TenantMiddleware:
            monkeypatch.setattr(middleware.kwargs['verifier'],'keys',Keys())
