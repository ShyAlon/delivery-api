"""Per-test tenants on the one migrated disposable server, never an external DSN.

Tasks are removed through the real store and auth sessions revoked at exit. Owner
and audit rows live until the session server is destroyed, preserving FK history.
JWTs use a local test key; the real signature/claims verifier and RLS still run.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import pg_harness
from delivery_api.postgres_store import PostgresStore
from delivery_api.supabase_auth import SupabaseIdentityVerifier
from delivery_api.tenancy import TenantScope, tenant_context

URL = 'https://fixture.supabase.co'
KEY = ec.generate_private_key(ec.SECP256R1())


class Keys:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


@dataclass
class Workspace:
    dsn: str
    store: PostgresStore
    scope: TenantScope

    def admin(self):
        import psycopg
        return psycopg.connect(self.dsn)

    @property
    def token(self):
        now = int(time.time())
        return jwt.encode(dict(sub=self.scope.owner_id, session_id=self.scope.session_id,
            exp=now+3600, iat=now, iss=URL+'/auth/v1', aud='authenticated',
            role='authenticated', email='fixture@example.test'), KEY, algorithm='ES256',
            headers={'kid':'fixture'})

    @property
    def headers(self):
        return {'Authorization':'Bearer '+self.token}


@pytest.fixture(scope='session')
def pg_workspace_factory():
    # Admin seeding/cleanup must never follow a caller-supplied DSN. Existing PG
    # integration tests may opt into one; this new factory deliberately cannot.
    if pg_harness.STATE['status'] != 'running':
        pytest.fail('PG workspace requires the local disposable harness: '+str(pg_harness.STATE))
    dsn = pg_harness.STATE['dsn']
    store = PostgresStore(dsn.replace('user=sandbox', 'user=delivery_app'))

    @contextmanager
    def factory():
        scope = TenantScope(str(uuid4()), str(uuid4()))
        workspace = Workspace(dsn, store, scope)
        with workspace.admin() as connection:
            connection.execute('INSERT INTO auth.users(id,email,email_confirmed_at) VALUES (%s,%s,now())',
                               (scope.owner_id, scope.owner_id+'@example.test'))
            connection.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',
                               (scope.session_id, scope.owner_id))
        try:
            yield workspace
        finally:
            try:
                with tenant_context(scope):
                    for task in store.list_tasks():
                        store.delete_task(task['task_id'])
            finally:
                with workspace.admin() as connection:
                    connection.execute('DELETE FROM auth.sessions WHERE id=%s', (scope.session_id,))
    return factory


@pytest.fixture
def pg_task_app(monkeypatch):
    """Full application routes/middleware, no lifespan engine/provider startup.

Wrap the already-imported app with production tenant authentication. The fixture
patches only the store/config and test signing key, never auth or scope functions.
"""
    import delivery_api.app as module
    from delivery_api.tenant_http import TenantMiddleware
    monkeypatch.setattr(module.config, 'CLOUD_TENANTS', True)

    def build(workspace):
        monkeypatch.setattr(module, 'store', workspace.store)
        monkeypatch.setattr(module.app.state, 'store', workspace.store)
        return TenantMiddleware(module.app,
            verifier=SupabaseIdentityVerifier(URL, key_provider=Keys()),
            session_check=workspace.store.auth_session_active,
            request_database=workspace.store._postgres)
    return build
