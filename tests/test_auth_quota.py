import time

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
    auth.limiter=auth.RateLimiter()
    monkeypatch.setattr(config,"SESSION_SECRET","")
    store=SQLiteStore(str(tmp_path/"local.db")); monkeypatch.setattr("delivery_api.app.store",store); app.state.store=store
    c=TestClient(app)
    link=c.post("/links",json={"founder_label":"local"},headers={"x-admin-token":config.ADMIN_TOKEN}).json()
    r=c.post("/runs",json={"token":link["token"],"free_text":"web app"})
    assert r.status_code==200
    assert c.get("/api/auth").json()=={"mode":"local","authenticated":True,"actor":"local user","admin":True,"providers":[],"workspace":"shared"}




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




def test_usage_tokens_are_read_from_pipeline_json(monkeypatch,tmp_path):
    store,c=_oauth(monkeypatch,tmp_path,quota=100)
    user=store.upsert_user("google","88","u@example.com","u",100,False)
    store.create_session("sid88",user["user_id"],time.time()+1000);c.cookies.set(config.SESSION_COOKIE,"sid88")
    link=store.create_link("u");store.attribute_run(link["run_id"],user["user_id"])
    r=c.post(f"/runs/{link['run_id']}/usage",json={"record_json":'{"usage":{"input_tokens":11,"output_tokens":7}}',"cost_usd":0},headers={"origin":"http://testserver"})
    assert r.status_code==200 and store.user_quota(user["user_id"])["tokens_used"]==18



def test_basic_signup_signin_wrong_password_and_session(monkeypatch,tmp_path):
    store=SQLiteStore(str(tmp_path/"basic.db"));app.state.store=store;monkeypatch.setattr("delivery_api.app.store",store)
    monkeypatch.setattr(config,"BASIC_AUTH_ENABLED",True);monkeypatch.setattr(config,"COOKIE_SECURE",False);auth.limiter=auth.RateLimiter()
    c=TestClient(app)
    assert c.get('/api/auth').json()['authenticated'] is False
    weak=c.post('/auth/signup',json={'username':'shy@example.com','password':'short'},headers={'origin':'http://testserver'})
    assert weak.status_code==400 and weak.json()['detail']['error']=='Password needs more strength.'
    good=c.post('/auth/signup',json={'username':'shy@example.com','password':'Strong pass 42!'},headers={'origin':'http://testserver'})
    assert good.status_code==200 and good.json()['actor']=='shy@example.com'
    assert store.basic_user('shy@example.com')['password_hash'].startswith('scrypt$')
    assert 'Strong pass 42!' not in store.basic_user('shy@example.com')['password_hash']
    c.cookies.clear()
    bad=c.post('/auth/signin',json={'username':'shy@example.com','password':'Wrong pass 42!'},headers={'origin':'http://testserver'})
    assert bad.status_code==401 and bad.json()['detail']['error']=='Sign-in did not work.'
    signed=c.post('/auth/signin',json={'username':'shy@example.com','password':'Strong pass 42!'},headers={'origin':'http://testserver'})
    assert signed.status_code==200
    c.cookies.update(signed.cookies);assert c.get('/api/auth').json()['actor']=='shy@example.com'
    assert c.post('/auth/logout',headers={'origin':'http://testserver'}).status_code==200
    assert c.get('/api/auth').json()['authenticated'] is False

def test_basic_email_validation_and_availability(monkeypatch,tmp_path):
    store=SQLiteStore(str(tmp_path/'email.db'));app.state.store=store;monkeypatch.setattr('delivery_api.app.store',store)
    monkeypatch.setattr(config,'BASIC_AUTH_ENABLED',True);monkeypatch.setattr(config,'COOKIE_SECURE',False);auth.limiter=auth.RateLimiter();c=TestClient(app)
    invalid=c.post('/auth/signup',json={'username':'not-an-email','password':'Strong pass 42!'},headers={'origin':'http://testserver'})
    assert invalid.status_code==400 and invalid.json()['detail']['error']=='Email address is not valid.'
    assert c.get('/auth/email-availability?email=shy@example.com').json()=={'email':'shy@example.com','valid':True,'available':True}
    assert c.post('/auth/signup',json={'username':'Shy@Example.com','password':'Strong pass 42!'},headers={'origin':'http://testserver'}).status_code==200
    assert c.get('/auth/email-availability?email=shy@example.com').json()['available'] is False

def test_concurrent_authenticated_posts_never_miss_valid_session(monkeypatch,tmp_path):
    """Regression for a shared embedded-replica connection under request concurrency."""
    import concurrent.futures,sqlite3,threading
    from delivery_api.store import TursoReplicaStore
    class SessionCursor:
        def __init__(self,owner,cursor):
            self.owner=owner;self.description=cursor.description;self.row=cursor.fetchone()
        def fetchone(self):
            with self.owner.cursor_lock:
                self.owner.active_session_fetches+=1
                overlap=self.owner.active_session_fetches>1
            time.sleep(.01)
            with self.owner.cursor_lock:self.owner.active_session_fetches-=1
            return None if overlap else self.row
    class Replica:
        def __init__(self,path):
            self.inner=sqlite3.connect(path,check_same_thread=False);self.inner.row_factory=None
            self.db_lock=threading.Lock();self.cursor_lock=threading.Lock();self.active_session_fetches=0
        def execute(self,sql,*args,**kwargs):
            if sql.startswith("SELECT s.*"):
                with self.db_lock:return SessionCursor(self,self.inner.execute(sql,*args,**kwargs))
            return self.inner.execute(sql,*args,**kwargs)
        def executemany(self,*a,**k):return self.inner.executemany(*a,**k)
        def executescript(self,*a,**k):return self.inner.executescript(*a,**k)
        def commit(self):return self.inner.commit()
        def sync(self):pass
    store=TursoReplicaStore(str(tmp_path/'auth-replica.db'),'libsql://unused','token',connection=Replica(tmp_path/'auth-replica.db'))
    app.state.store=store;monkeypatch.setattr('delivery_api.app.store',store);monkeypatch.setattr(config,'BASIC_AUTH_ENABLED',True);monkeypatch.setattr(config,'COOKIE_SECURE',False);monkeypatch.setattr(config,'RUN_START_RATE_LIMIT',1000);auth.limiter=auth.RateLimiter()
    user=store.upsert_user('google','concurrent','shy.alon@gmail.com','shy.alon@gmail.com',1000,False);store.create_session('stable-session',user['user_id'],time.time()+1000)
    task=store.create_task('Concurrent answers')['task_id'];store.set_engine_session(task,'concurrent-engine-session');store.update_phase(task,'intent','running')
    from uuid import uuid4
    from vmodel_contracts.interactive import EngineSnapshot,InputKind,InputRequest,NextStep,ProcessStatus,SubmitInputResponse,InteractiveEvent,EventType
    pending=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Answer')
    class ConcurrentEngine:
        def snapshot(self,_):return EngineSnapshot(session_id='concurrent-engine-session',task_id=task,sequence=2,process_status=ProcessStatus.WAITING_INPUT,next_step=NextStep.WAIT_INPUT,open_input=pending)
        def submit(self,_,response):
            return SubmitInputResponse(accepted_event=InteractiveEvent(event_id=uuid4(),session_id='concurrent-engine-session',task_id=task,phase_id=1,sequence=3,event_type=EventType.INPUT_ACCEPTED,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN),snapshot=EngineSnapshot(session_id='concurrent-engine-session',task_id=task,sequence=3,process_status=ProcessStatus.RUNNING,next_step=NextStep.RUN))
    monkeypatch.setattr('delivery_api.app.engine_client',ConcurrentEngine())
    def post(i):
        c=TestClient(app);c.cookies.set(config.SESSION_COOKIE,'stable-session')
        return c.post(f'/api/tasks/{task}/answer',json={'answer':f'answer {i}'},headers={'origin':'http://testserver'}).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses=list(pool.map(post,range(24)))
    assert statuses==[200]*24
    # Do not leave the process-global limiter full for later API tests.
    auth.limiter=auth.RateLimiter()

def test_run_start_limiter_does_not_rate_limit_interview_answers(monkeypatch,pg_workspace_factory,pg_task_app):
    from delivery_api.tenancy import tenant_context
    # Test only admitted request dependency ordering,no engine/model execution.
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,"installed",lambda:True)
    monkeypatch.setattr(auth,"limiter",auth.RateLimiter())
    monkeypatch.setattr(config,"RUN_START_RATE_LIMIT",1)
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            task=workspace.store.create_task("Answer rate scope")["task_id"]
        c=TestClient(pg_task_app(workspace))
        from delivery_api import app as app_module
        monkeypatch.setattr(app_module.runner,"input_state",lambda _:{"decision":None,"process_live":False,"waiting":False})
        # A stale answer reaches its domain conflict repeatedly,never 429.
        for _ in range(3):
            r=c.post(f"/api/tasks/{task}/answer",json={"answer":"same"},headers=workspace.headers)
            assert r.status_code==409


def test_run_start_dependency_rate_limits_actual_starts(monkeypatch,pg_workspace_factory,pg_task_app):
    # Test only admitted request dependency ordering,no engine/model execution.
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,"installed",lambda:True)
    monkeypatch.setattr(auth,"limiter",auth.RateLimiter())
    monkeypatch.setattr(config,"RUN_START_RATE_LIMIT",1)
    with pg_workspace_factory() as workspace:
        c=TestClient(pg_task_app(workspace))
        # Invalid task IDs prove dependency bucket ownership without model work.
        first=c.post("/api/tasks/missing/start",json={"model_route":"ollama"},headers=workspace.headers)
        second=c.post("/api/tasks/missing/start",json={"model_route":"ollama"},headers=workspace.headers)
        assert first.status_code==404
        assert second.status_code==429 and "run-start" in second.text


def test_rate_limit_contracts_require_signed_pg_fixture():
    import inspect
    for case in (test_run_start_limiter_does_not_rate_limit_interview_answers,
                 test_run_start_dependency_rate_limits_actual_starts):
        assert 'pg_workspace_factory' in inspect.signature(case).parameters
        assert 'pg_task_app' in inspect.signature(case).parameters


def test_pg_rate_contract_setup_preserves_closed_runtime_boundary(monkeypatch,pg_workspace_factory,pg_task_app):
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'installed',lambda:False)
    with pg_workspace_factory() as workspace:
        c=TestClient(pg_task_app(workspace))
        for endpoint in ('start','answer'):
            r=c.post('/api/tasks/missing/'+endpoint,json={},headers=workspace.headers)
            assert r.status_code==503
            assert r.json()['detail']=='Cloud execution pending scoped worker protocol'
        monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
        for endpoint in ('start','answer'):
            assert c.post('/api/tasks/missing/'+endpoint,json={}).status_code==401
