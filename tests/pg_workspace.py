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

    @contextmanager
    def factory():
        # Pool lifecycle tests deliberately close the shared pool. Each new
        # workspace borrows the current process pool, not a session-old handle.
        store=PostgresStore(dsn.replace('user=sandbox','user=delivery_app'))
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
                        try:
                            store.delete_task(task['task_id'])
                        except ValueError as exc:
                            # Preserve production refusal, never mutate running/accounted
                            # state just to clean a fixture. Server exit removes it.
                            if str(exc) not in {
                                'a running or paused task cannot be deleted',
                                'Stop is pending; worker and provider quiescence must be confirmed before Delete',
                                'task has provider ledger history; preserve it for reconciliation'}:
                                raise
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
        # State stores values in its mapping, not __dict__. Patch that mapping
        # so pytest restores rather than deletes the dynamic attribute.
        monkeypatch.setitem(module.app.state._state, 'store', workspace.store)
        monkeypatch.setattr(module.orchestrator, 'store', workspace.store)
        # The app's own middleware was built before pool-lifecycle tests closed
        # its captured store. Bind every auth/request borrower to this workspace.
        for middleware in module.app.user_middleware:
            if middleware.cls is TenantMiddleware:
                monkeypatch.setitem(middleware.kwargs,'verifier',SupabaseIdentityVerifier(URL,key_provider=Keys()))
                monkeypatch.setitem(middleware.kwargs,'session_check',workspace.store.auth_session_active)
                monkeypatch.setitem(middleware.kwargs,'request_database',workspace.store._postgres)
        monkeypatch.setattr(module.app,'middleware_stack',None)
        return TenantMiddleware(module.app,
            verifier=SupabaseIdentityVerifier(URL, key_provider=Keys()),
            session_check=workspace.store.auth_session_active,
            request_database=workspace.store._postgres)
    return build


@pytest.fixture
def pg_browser(pg_workspace_factory, pg_task_app, tmp_path):
    """Fresh browser and authenticated workspace, real local ASGI HTTP server."""
    import asyncio
    import os
    import socket
    import threading
    import uvicorn
    from playwright.sync_api import sync_playwright

    with pg_workspace_factory() as workspace:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(pg_task_app(workspace), host='127.0.0.1',
            port=port, lifespan='off', log_level='warning'))
        loops = []
        async def serve():
            loops.append(asyncio.get_running_loop())
            await server.serve()
        thread = threading.Thread(target=lambda: asyncio.run(serve()))
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.02)
            assert server.started, 'test server did not start'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True,
                    executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                try:
                    context = browser.new_context(viewport={'width':1365,'height':900},
                                                  reduced_motion='reduce')
                    context.add_cookies([{'name':'requi_access','value':workspace.token,'url':base}])
                    page = context.new_page()
                    try:
                        yield page, base, workspace
                    finally:
                        screenshot = tmp_path/'pg-browser-parity.png'
                        page.screenshot(path=str(screenshot), full_page=True)
                        print('PG_BROWSER_PARITY_SCREENSHOT='+str(screenshot))
                finally:
                    browser.close()
        finally:
            # Lifespan is disabled here, so mirror its stream-stop notification
            # on the server loop before waiting for uvicorn's graceful drain.
            from delivery_api import app as app_module
            def stop_streams():
                for stop in tuple(app_module._stream_shutdown_events):
                    stop.set()
            if loops:
                loops[0].call_soon_threadsafe(stop_streams)
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'test server did not stop'


@pytest.fixture
def pg_scoped_workspace(pg_workspace_factory,monkeypatch):
    """Direct-helper PG tests; HTTP authentication is covered by pg_task_app."""
    from delivery_api import app as module
    with pg_workspace_factory() as workspace:
        monkeypatch.setattr(module,'store',workspace.store)
        monkeypatch.setitem(module.app.state._state,'store',workspace.store)
        monkeypatch.setattr(module.orchestrator,'store',workspace.store)
        from pg_storage_guard import assert_pg_storage_only
        assert_pg_storage_only()
        with tenant_context(workspace.scope):
            yield workspace
