"""Session liveness cache must bind the verified owner as well as session."""
from fastapi.testclient import TestClient
import jwt,time
from delivery_api.tenancy import tenant_context
from pg_workspace import KEY,URL


def test_warmed_foreign_session_never_authenticates_another_owner(pg_workspace_factory,pg_task_app):
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store
        with tenant_context(a.scope):tid=s.create_task('Owner-bound session cache')['task_id']
        app=pg_task_app(a)
        # No lifespan: test the real JWT verifier, request boundary, liveness and
        # RLS without starting engine/provider/background activity.
        client=TestClient(app)
        assert client.get('/api/tasks',headers=b.headers).status_code==200
        claims=dict(sub=a.scope.owner_id,session_id=b.scope.session_id,
            exp=int(time.time())+600,iat=int(time.time()),iss=URL+'/auth/v1',
            aud='authenticated',role='authenticated')
        wrong={'Authorization':'Bearer '+jwt.encode(claims,KEY,algorithm='ES256')}
        assert client.get('/api/tasks',headers=wrong).status_code==401
        assert client.get('/api/tasks/'+tid,headers=wrong).status_code==401
        # A mismatched denial must not poison either legitimate identity.
        assert client.get('/api/tasks',headers=b.headers).status_code==200
        assert client.get('/api/tasks/'+tid,headers=a.headers).status_code==200
        client.close()


def test_cache_owner_pair_preserves_ttl_and_negative_entries():
    from delivery_api.tenant_http import SessionCheckCache
    answers=iter([True,False,True]);calls=[];clock=[100.0]
    def check():calls.append(1);return next(answers)
    cache=SessionCheckCache(check);cache._time=lambda:clock[0]
    assert cache.active('B','session-B') is True
    assert cache.active('A','session-B') is False
    assert cache.active('B','session-B') is True
    assert cache.active('A','session-B') is False
    assert len(calls)==2
    clock[0]=130.0
    assert cache.active('A','session-B') is True
    assert len(calls)==3
