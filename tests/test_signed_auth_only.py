from fastapi.testclient import TestClient
from delivery_api import auth,config
import inspect


def test_identity_never_depends_on_cloud_setting(pg_scoped_workspace,pg_task_app,monkeypatch):
    app=pg_task_app(pg_scoped_workspace)
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    unsigned=TestClient(app)
    assert unsigned.get('/api/auth').json()['authenticated'] is False
    assert unsigned.get('/api/tasks').status_code==401
    signed=TestClient(app,headers=pg_scoped_workspace.headers)
    assert signed.get('/api/auth').json()['mode']=='supabase'
    assert signed.get('/api/tasks').status_code==200


def test_no_basic_or_oauth_identity_implementation():
    for name in ('basic_signup','basic_signin','hash_password','verify_password','oauth_start','oauth_callback','refresh_session'):
        assert not hasattr(auth,name)
    assert 'CLOUD_TENANTS' not in inspect.getsource(auth.actor)
