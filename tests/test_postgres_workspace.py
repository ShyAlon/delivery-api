"""Phase-1 PG fixture contracts. Existing SQLite tests remain unchanged."""
from contextlib import closing

import pytest
from delivery_api.tenancy import tenant_context


def test_factory_uses_live_distinct_sessions_and_restricted_role(pg_workspace_factory):
    with pg_workspace_factory() as first, pg_workspace_factory() as second:
        assert first.scope.owner_id != first.scope.session_id
        assert first.scope.owner_id != second.scope.owner_id
        with tenant_context(first.scope):
            assert first.store.auth_session_active()
            task = first.store.create_task('Same title')
        with tenant_context(second.scope):
            assert second.store.auth_session_active()
            assert second.store.list_tasks() == []
            assert second.store.task(task['task_id']) is None
            assert second.store.create_task('Same title')['owner_id'] == second.scope.owner_id
        with tenant_context(first.scope), first.store._postgres.transaction() as connection:
            row = connection.execute('SELECT current_user, session_user').fetchone()
            assert row['current_user'] == 'requi_backend'
            assert row['session_user'] == 'delivery_app'
    with first.admin() as connection:
        assert connection.execute('SELECT count(*) FROM auth.sessions WHERE id=%s', (first.scope.session_id,)).fetchone()[0] == 0


def test_task_api_lifecycle_and_owner_isolation(pg_workspace_factory, pg_task_app):
    from fastapi.testclient import TestClient
    with pg_workspace_factory() as first, pg_workspace_factory() as second:
        with closing(TestClient(pg_task_app(first))) as client:
            assert client.get('/api/tasks').status_code == 401
            assert client.get('/api/tasks', headers={'Authorization':'Bearer broken'}).status_code == 401
            headers = first.headers
            response = client.post('/api/tasks', json={'title':'Lifecycle', 'brief':'A measured PG task'}, headers=headers)
            assert response.status_code == 201, response.text
            task_id = response.json()['task_id']
            assert response.json()['owner_id'] == first.scope.owner_id
            collision = client.post('/api/tasks', json={'title':'LIFECYCLE'}, headers=headers)
            assert collision.status_code == 409
            assert set(collision.json()['detail']) == {'error','cause','next'}
            assert client.get('/api/tasks', headers=headers).json()['tasks'][0]['task_id'] == task_id
            assert client.patch('/api/tasks/'+task_id, json={'title':'Renamed'}, headers=headers).json()['title'] == 'Renamed'
            assert client.get('/api/tasks', headers=second.headers).json() == {'tasks':[]}
            assert client.patch('/api/tasks/'+task_id, json={'title':'Stolen'}, headers=second.headers).status_code == 404
            deleted = client.request('DELETE','/api/tasks/'+task_id, headers=headers,json={'confirm':True})
            assert deleted.status_code == 200
            assert deleted.json()['source_artifacts_deleted'] is False
            assert client.get('/api/tasks/'+task_id, headers=headers).status_code == 404
            assert client.get('/api/tasks', headers=headers).json() == {'tasks':[]}


def test_pg_dashboard_real_browser(pg_workspace_factory, pg_task_app, tmp_path):
    import os
    import socket
    import threading
    import time
    import uvicorn
    from playwright.sync_api import sync_playwright

    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            workspace.store.create_task('Postgres browser task', 'Signed JWT, live session, RLS')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(pg_task_app(workspace), host='127.0.0.1',
            port=port, lifespan='off', log_level='warning'))
        thread = threading.Thread(target=server.run)
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
                    context = browser.new_context(viewport={'width':1365, 'height':900})
                    context.add_cookies([{'name':'requi_access', 'value':workspace.token,
                        'url':f'http://127.0.0.1:{port}'}])
                    page = context.new_page()
                    page.goto(f'http://127.0.0.1:{port}/')
                    page.locator('.task h2', has_text='Postgres browser task').wait_for()
                    assert page.locator('.task').count() == 1
                    page.locator('#taskfilter').fill('not present')
                    assert page.locator('.task').count() == 0
                    page.locator('#taskfilter').fill('Postgres browser task')
                    assert page.locator('.task').count() == 1
                    screenshot = tmp_path/'postgres-dashboard.png'
                    page.emulate_media(reduced_motion='reduce')
                    page.wait_for_function("[...document.querySelectorAll('.task')].every(e=>Number(getComputedStyle(e).opacity)===1)")
                    page.screenshot(path=str(screenshot), full_page=True)
                    print('PG_BROWSER_SCREENSHOT='+str(screenshot))
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'test server did not stop'


def test_factory_revokes_session_and_cleans_tasks_on_failure(pg_workspace_factory):
    with pytest.raises(RuntimeError, match='test body failed'):
        with pg_workspace_factory() as workspace:
            with tenant_context(workspace.scope):
                task = workspace.store.create_task('Cleanup after failure')
            raise RuntimeError('test body failed')
    with workspace.admin() as connection:
        assert connection.execute('SELECT count(*) FROM auth.sessions WHERE id=%s',
            (workspace.scope.session_id,)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM tasks WHERE task_id=%s AND deleted_at IS NULL',
            (task['task_id'],)).fetchone()[0] == 0


def test_factory_refuses_external_database_before_any_connect(monkeypatch):
    import pg_workspace
    monkeypatch.setitem(pg_workspace.pg_harness.STATE, 'status', 'external')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not connect to external DB')
    monkeypatch.setattr(pg_workspace, 'PostgresStore', forbidden)
    with pytest.raises(pytest.fail.Exception, match='local disposable harness'):
        pg_workspace.pg_workspace_factory.__wrapped__()
