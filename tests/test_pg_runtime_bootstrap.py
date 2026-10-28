"""PG-first runtime fixture imports the real application without opening SQLite."""
import json
import pytest


def test_runtime_fixture_selects_postgres_and_never_opens_sqlite(pg_runtime_process):
    result=pg_runtime_process('''
import json
from delivery_api import app,config
from delivery_api.postgres_store import PostgresStore
assert isinstance(app.store,PostgresStore)
assert config.CLOUD_TENANTS is True
assert app.app.state.store is app.store
assert app.orchestrator.store is app.store
print(json.dumps({'pg':True,'dsn_configured':bool(config.DATABASE_URL)}))
''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])=={'pg':True,'dsn_configured':True}


def test_pg_runtime_fixture_rejects_external_dsn(pg_runtime_environment):
    with pytest.raises(ValueError,match='disposable'):
        pg_runtime_environment(dsn='postgresql://not-a-test-host/production')


def test_signed_api_fixture_rejects_expired_wrong_and_revoked_session(pg_workspace_factory,pg_task_app):
    import time
    from uuid import uuid4
    import jwt
    from fastapi.testclient import TestClient
    from pg_workspace import KEY,URL
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        claims=dict(sub=a.scope.owner_id,session_id=a.scope.session_id,exp=int(time.time())+600,
            iat=int(time.time()),iss=URL+'/auth/v1',aud='authenticated',role='authenticated')
        def signed(**overrides):return {'Authorization':'Bearer '+jwt.encode(claims|overrides,KEY,algorithm='ES256')}
        app=pg_task_app(a)
        with TestClient(app) as client:
            created=client.post('/api/tasks',headers=a.headers,json={'title':'PG fixture auth'})
            assert created.status_code==201,created.text
            tid=created.json()['task_id']
            assert client.get('/api/tasks/'+tid,headers=b.headers).status_code==404
            assert client.get('/api/tasks',headers=signed(exp=int(time.time())-1)).status_code==401
            assert client.get('/api/tasks',headers=signed(session_id=str(uuid4()))).status_code==401
            assert client.get('/api/tasks',headers=signed(session_id=b.scope.session_id)).status_code==401
            with a.admin() as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(a.scope.session_id,))
            # Session liveness is intentionally cached for 30 seconds. Advance
            # the test clock past that TTL, without changing production behavior.
            app.session_check._time=lambda:10**20
            assert client.get('/api/tasks',headers=a.headers).status_code==401
