"""Rolling server-side sessions rotate once; expiry and revocation win races."""
import time
from fastapi.testclient import TestClient
from delivery_api import auth,config
from delivery_api.app import app
from delivery_api.store import SQLiteStore


def _client(monkeypatch,tmp_path):
    store=SQLiteStore(str(tmp_path/'refresh.db'))
    app.state.store=store
    monkeypatch.setattr('delivery_api.app.store',store)
    monkeypatch.setattr(config,'SESSION_SECRET','s'*40)
    monkeypatch.setattr(config,'GOOGLE_CLIENT_ID','mock-id')
    monkeypatch.setattr(config,'COOKIE_SECURE',False)
    user=store.upsert_user('google','one','u@example.com','User',100,False)
    return store,TestClient(app),user


def test_refresh_rotates_handle_and_renews_expiry(monkeypatch,tmp_path):
    store,client,user=_client(monkeypatch,tmp_path)
    monkeypatch.setattr(config,'SESSION_TTL_SECONDS',43200)
    now=time.time();store.create_session('original',user['user_id'],now+20)
    client.cookies.set(config.SESSION_COOKIE,'original',domain='testserver.local')
    reply=client.post('/auth/refresh',headers={'origin':'http://testserver'})
    assert reply.status_code==200 and reply.json()['status']=='refreshed'
    assert reply.json()['expires_at']>now+43000
    new=reply.cookies[config.SESSION_COOKIE]
    assert new!='original' and store.session('original') is None
    assert store.session(new)['expires_at']>now+43000
    assert reply.headers['cache-control']=='no-store'
    assert 'httponly' in reply.headers['set-cookie'].lower()
    assert client.get('/api/auth').json()['authenticated']
    store.delete_session(new)
    assert client.post('/auth/refresh',headers={'origin':'http://testserver'}).status_code==401


def test_refresh_rejects_expired_and_replay_without_leaking_new_session(monkeypatch,tmp_path):
    store,client,user=_client(monkeypatch,tmp_path)
    store.create_session('expired',user['user_id'],time.time()-1)
    client.cookies.set(config.SESSION_COOKIE,'expired',domain='testserver.local')
    assert client.post('/auth/refresh',headers={'origin':'http://testserver'}).status_code==401
    store.create_session('single',user['user_id'],time.time()+100)
    client.cookies.set(config.SESSION_COOKIE,'single',domain='testserver.local')
    assert client.post('/auth/refresh',headers={'origin':'http://testserver'}).status_code==200
    replacement=client.cookies.get(config.SESSION_COOKIE,domain='testserver.local')
    client.cookies.set(config.SESSION_COOKIE,'single',domain='testserver.local')
    assert client.post('/auth/refresh',headers={'origin':'http://testserver'}).status_code==401
    assert store.session(replacement) is not None


def test_refresh_is_csrf_protected_and_local_mode_cannot_create_session(monkeypatch,tmp_path):
    store,client,user=_client(monkeypatch,tmp_path)
    store.create_session('csrf',user['user_id'],time.time()+100)
    client.cookies.set(config.SESSION_COOKIE,'csrf',domain='testserver.local')
    assert client.post('/auth/refresh',headers={'origin':'https://elsewhere.test'}).status_code==403
    assert store.session('csrf') is not None
    monkeypatch.setattr(config,'SESSION_SECRET','')
    assert client.post('/auth/refresh',headers={'origin':'http://testserver'}).status_code==400
