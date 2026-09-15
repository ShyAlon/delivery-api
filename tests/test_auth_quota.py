import hashlib
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from delivery_api import auth, config
from delivery_api.app import app
from delivery_api.store import SQLiteStore


def _oauth(monkeypatch, tmp_path, quota=100):
    store=SQLiteStore(str(tmp_path/"auth.db")); app.state.store=store
    monkeypatch.setattr("delivery_api.app.store",store)
    monkeypatch.setattr(config,"SESSION_SECRET","test-secret")
    monkeypatch.setattr(config,"COOKIE_SECURE",False)
    monkeypatch.setattr(config,"GOOGLE_CLIENT_ID","google-id")
    monkeypatch.setattr(config,"GOOGLE_CLIENT_SECRET","google-secret")
    monkeypatch.setattr(config,"DEFAULT_USER_TOKEN_QUOTA",quota)
    auth.limiter=auth.RateLimiter()
    return store,TestClient(app)


def test_local_mode_bypass_unchanged(monkeypatch,tmp_path):
    monkeypatch.setattr(config,"SESSION_SECRET","")
    store=SQLiteStore(str(tmp_path/"local.db")); monkeypatch.setattr("delivery_api.app.store",store); app.state.store=store
    c=TestClient(app)
    link=c.post("/links",json={"founder_label":"local"},headers={"x-admin-token":config.ADMIN_TOKEN}).json()
    r=c.post("/runs",json={"token":link["token"],"free_text":"web app"})
    assert r.status_code==200
    assert c.get("/api/auth").json()=={"mode":"local","authenticated":True,"actor":"local user","admin":True,"providers":[]}


def test_google_oauth_state_session_and_logout(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path)
    start=c.get("/auth/google/start",follow_redirects=False)
    state=dict(__import__("urllib.parse").parse.parse_qsl(__import__("urllib.parse").parse.urlsplit(start.headers["location"]).query))["state"]
    class Resp:
        def __init__(self,data):self.data=data
        def json(self):return self.data
    monkeypatch.setattr("delivery_api.auth.httpx.post",lambda *a,**k:Resp({"access_token":"token"}))
    monkeypatch.setattr("delivery_api.auth.httpx.get",lambda *a,**k:Resp({"sub":"42","email":"shy@example.com","name":"Shy"}))
    r=c.get(f"/auth/google/callback?code=ok&state={state}",follow_redirects=False)
    assert r.status_code==303 and config.SESSION_COOKIE in r.cookies
    c.cookies.update(r.cookies)
    assert c.get("/api/auth").json()["actor"]=="shy@example.com"
    assert c.post("/auth/logout",headers={"origin":"http://testserver"}).status_code==200
    assert c.get("/api/auth").json()["authenticated"] is False


def test_exhausted_user_rejected_and_quota_decision_audited(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path,quota=10)
    user=store.upsert_user("google","7","u@example.com","u@example.com",10,False)
    sid="sid"; store.create_session(sid,user["user_id"],time.time()+1000); c.cookies.set(config.SESSION_COOKIE,sid)
    link=store.create_link("u")
    store.add_user_usage(link["run_id"],user["user_id"],"{}",0,6,4)
    r=c.post("/runs",json={"token":link["token"],"free_text":"web app"},headers={"origin":"http://testserver"})
    assert r.status_code==402 and r.json()["detail"]["error"]=="Token budget exhausted."
    entry=store.audit_entries()[0]
    assert entry["action"]=="quota.checked" and entry["detail"]["allowed"] is False


def test_admin_can_change_user_quota(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path)
    user=store.upsert_user("github","9","u@example.com","u",100,False)
    admin=store.upsert_user("google","1","admin@example.com","admin",100,True)
    store.create_session("adminsid",admin["user_id"],time.time()+1000); c.cookies.set(config.SESSION_COOKIE,"adminsid")
    r=c.put(f"/api/admin/users/{user['user_id']}/quota",json={"token_quota":500},headers={"origin":"http://testserver"})
    assert r.status_code==200 and r.json()["token_quota"]==500
    assert store.audit_entries()[0]["action"]=="quota.configured"


def test_usage_that_crosses_quota_is_rejected(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path,quota=10)
    user=store.upsert_user("google","8","u@example.com","u",10,False)
    store.create_session("sid8",user["user_id"],time.time()+1000); c.cookies.set(config.SESSION_COOKIE,"sid8")
    link=store.create_link("u"); store.attribute_run(link["run_id"],user["user_id"])
    r=c.post(f"/runs/{link['run_id']}/usage",json={"record_json":"{}","cost_usd":0,"input_tokens":8,"output_tokens":3},headers={"origin":"http://testserver"})
    assert r.status_code==402
    assert store.user_quota(user["user_id"])["tokens_used"]==0


def test_github_oauth_fetches_verified_email(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path)
    monkeypatch.setattr(config,"GITHUB_CLIENT_ID","github-id")
    monkeypatch.setattr(config,"GITHUB_CLIENT_SECRET","github-secret")
    start=c.get("/auth/github/start",follow_redirects=False)
    from urllib.parse import parse_qsl,urlsplit
    state=dict(parse_qsl(urlsplit(start.headers["location"]).query))["state"]
    class Resp:
        def __init__(self,data):self.data=data
        def json(self):return self.data
    monkeypatch.setattr("delivery_api.auth.httpx.post",lambda *a,**k:Resp({"access_token":"token"}))
    def get(url,*a,**k):
        return Resp([{"email":"dev@example.com","verified":True,"primary":True}]) if url.endswith("/emails") else Resp({"id":99,"login":"dev","email":None})
    monkeypatch.setattr("delivery_api.auth.httpx.get",get)
    r=c.get(f"/auth/github/callback?code=ok&state={state}",follow_redirects=False)
    assert r.status_code==303
    assert store.user_quota("github:99")["email"]=="dev@example.com"


def test_usage_tokens_are_read_from_pipeline_json(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path,quota=100)
    user=store.upsert_user("google","88","u@example.com","u",100,False)
    store.create_session("sid88",user["user_id"],time.time()+1000);c.cookies.set(config.SESSION_COOKIE,"sid88")
    link=store.create_link("u");store.attribute_run(link["run_id"],user["user_id"])
    r=c.post(f"/runs/{link['run_id']}/usage",json={"record_json":'{"usage":{"input_tokens":11,"output_tokens":7}}',"cost_usd":0},headers={"origin":"http://testserver"})
    assert r.status_code==200 and store.user_quota(user["user_id"])["tokens_used"]==18


def test_session_cookie_handle_is_not_stored_verbatim(monkeypatch,tmp_path):
    store,_=_oauth(monkeypatch,tmp_path)
    user=store.upsert_user("google","200","u@example.com","u",10,False)
    store.create_session("opaque-cookie-value",user["user_id"],time.time()+100)
    stored=store._db.execute("SELECT session_id FROM sessions").fetchone()["session_id"]
    assert stored != "opaque-cookie-value"
    assert store.session("opaque-cookie-value")["user_id"]==user["user_id"]
