"""The operator pages: signed-out visitors reach the sign-in page, CPU and the signed-in person show when no run is active, the log comes as a zip,
every table keeps its column headers, and the pages share one header, navigation and footer."""
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from delivery_api import config, ops_health, run_metrics, run_slots, server_log_buffer
from delivery_api.tenancy import TenantAccessDenied, TenantScope
from delivery_api.tenant_http import TenantMiddleware

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
A = "00000000-0000-0000-0000-000000000001"
HTML = {"Accept": "text/html,application/xhtml+xml"}
GOOD = {"Cookie": "requi_access=a"}
BAD = {"Cookie": "requi_access=expired"}


class Verifier:
    def verify(self, token):
        if token != "a":
            raise TenantAccessDenied("bad")
        return TenantScope(A)


def _app(session_ok=lambda: True):
    app = FastAPI()
    for path in ("/", "/sessions", "/ops", "/credits", "/ops/ops.js", "/api/data"):
        app.add_api_route(path, lambda: JSONResponse({"served": True}), methods=["GET", "POST"])
    app.add_middleware(TenantMiddleware, verifier=Verifier(), session_check=session_ok)
    return TestClient(app, follow_redirects=False)


# ---- 1. signed-out visitors reach the sign-in page, not a JSON error ----

@pytest.mark.parametrize("path", ["/ops", "/credits"])
def test_a_signed_out_browser_is_sent_to_sign_in_and_returns_afterwards(path):
    with _app() as client:
        response = client.get(path, headers=HTML)
        assert response.status_code == 302 and response.headers["location"] == "/?next=" + path
        assert response.headers["cache-control"] == "no-store"


def test_scripts_and_api_calls_keep_the_json_refusal():
    with _app() as client:
        for headers in ({}, {"Accept": "application/json"}, {"Accept": "*/*"}):
            for path in ("/ops", "/credits", "/api/data"):
                response = client.get(path, headers=headers)
                assert response.status_code == 401 and response.json() == {"detail": "Supabase sign-in required"}
        assert client.get("/api/data", headers=HTML).status_code == 401      # an API path is never redirected, whatever the browser accepts
        assert client.get("/ops/ops.js", headers=HTML).status_code == 401    # the script is not a page
        assert client.post("/ops", headers=HTML).status_code == 401          # only opening a page is redirected


def test_an_expired_or_damaged_session_opens_public_pages_signed_out_and_redirects_operator_pages():
    with _app() as client:
        for headers in (BAD, {"Authorization": "Bearer"}, {"Authorization": "Basic zzz"}):
            assert client.get("/", headers={**HTML, **headers}).status_code == 200
            assert client.get("/sessions", headers={**HTML, **headers}).status_code == 200
            assert client.get("/ops", headers={**HTML, **headers}).status_code == 302
            assert client.get("/credits", headers={**HTML, **headers}).headers["location"] == "/?next=/credits"
            assert client.get("/api/data", headers={**HTML, **headers}).status_code == 401
        assert client.get("/api/data", headers=BAD).json() == {"detail": "Invalid or expired access token"}


def test_a_revoked_session_behaves_the_same_way():
    with _app(session_ok=lambda: False) as client:
        assert client.get("/", headers={**HTML, **GOOD}).status_code == 200
        redirect = client.get("/credits", headers={**HTML, **GOOD})
        assert redirect.status_code == 302 and redirect.headers["location"] == "/?next=/credits"
        assert client.get("/api/data", headers=GOOD).json() == {"detail": "Session expired or revoked"}


def test_a_signed_in_person_gets_the_operator_page_itself():
    with _app() as client:
        assert client.get("/ops", headers={**HTML, **GOOD}).status_code == 200
        assert client.get("/credits", headers={**HTML, **GOOD}).status_code == 200


def _node(source):
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0, reason="needs node")
def test_after_sign_in_only_the_fixed_pages_come_back():
    out = _node("import {returnPage} from '%s';console.log(JSON.stringify(['?next=/ops','?next=/credits','?next=//evil.example','?next=https://evil.example/','?next=/ops/ops.js','?next=/tasks/1/view','','?x=1'].map(returnPage)))" % (WEB / "return-page.js").as_uri())
    assert out == ["/ops", "/credits", "/", "/", "/", "/", "/", "/"]


# ---- 2. CPU and memory are read on a timer ----

@pytest.fixture
def metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_METRICS_DB", str(tmp_path / "metrics.db"))
    run_metrics.reset(); run_slots.reset()
    yield
    run_metrics.stop_idle_sampler(); run_metrics.reset(); run_slots.reset()


def test_container_readings_are_stored_with_no_run_active(metrics, monkeypatch):
    monkeypatch.setattr(run_slots, "host_capacity", lambda: {"mem_total_mb": 3072, "mem_available_mb": 2000, "cpus": 2})
    readings = iter([10.0, 11.5])
    monkeypatch.setattr(run_metrics, "_container_cpu_seconds", lambda: next(readings))
    assert run_metrics.sample_container(1000.0)["cpu_pct"] is None            # the first reading has nothing to compare with
    assert run_metrics.sample_container(1030.0)["cpu_pct"] == 5.0            # 1.5 CPU seconds in 30 seconds
    assert run_slots.snapshot()["running"] == [] or not run_slots.snapshot()["running"]


def test_the_timer_samples_by_itself_and_stops(metrics, monkeypatch):
    monkeypatch.setattr(run_slots, "host_capacity", lambda: {"mem_total_mb": 3072, "mem_available_mb": 2000, "cpus": 2})
    values = iter(range(1, 1000))
    monkeypatch.setattr(run_metrics, "_container_cpu_seconds", lambda: float(next(values)))
    assert run_metrics.start_idle_sampler(interval_s=0.05) is True
    assert run_metrics.start_idle_sampler(interval_s=0.05) is False           # a second start does nothing
    deadline = time.time() + 5
    while time.time() < deadline:
        data = run_metrics.overview()
        if data["container"] and data["container"]["last"].get("cpu_pct") is not None:
            break
        time.sleep(0.05)
    run_metrics.stop_idle_sampler()
    assert data["container"]["last"]["cpu_pct"] is not None and data["container"]["last"]["cpu_pct"] >= 0
    health = ops_health.health()
    assert health["cpu"]["container_percent"] is not None and health["cpu"]["average_24h_percent"] is not None


def test_an_unreadable_cpu_file_is_not_an_error(metrics, monkeypatch):
    monkeypatch.setattr(run_slots, "host_capacity", lambda: {"mem_total_mb": 3072, "mem_available_mb": 2000, "cpus": 2})
    monkeypatch.setattr(run_metrics, "_container_cpu_seconds", lambda: None)
    assert run_metrics.sample_container(1000.0)["cpu_pct"] is None
    assert run_metrics.sample_container(1030.0)["cpu_pct"] is None


# ---- 3. the signed-in person's email ----

def test_the_email_reaches_the_actor_when_the_verifier_gives_it():
    class WithEmail(Verifier):
        def verify_with_email(self, token):
            return self.verify(token), False, "ann@example.com"
    app = FastAPI()

    @app.get("/who")
    def who(request: __import__("starlette.requests", fromlist=["Request"]).Request):
        actor = request.state.supabase_actor
        return {"email": actor.email, "label": actor.label}
    app.add_middleware(TenantMiddleware, verifier=WithEmail())
    with TestClient(app) as client:
        assert client.get("/who", headers={"Authorization": "Bearer a"}).json() == {"email": "ann@example.com", "label": "Signed-In User"}


def test_the_real_verifier_returns_the_signed_email_and_none_when_the_token_has_none(monkeypatch):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from delivery_api.supabase_auth import SupabaseIdentityVerifier
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://abc.supabase.co/auth/v1"
    monkeypatch.setattr(config, "OPERATOR_EMAIL", "shy@example.com")

    class Keys:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    verifier = SupabaseIdentityVerifier("https://abc.supabase.co", key_provider=Keys())
    now = int(time.time())

    def token(**extra):
        claims = {"sub": A, "exp": now + 600, "iat": now - 5, "iss": issuer, "aud": "authenticated", "role": "authenticated", "session_id": "00000000-0000-0000-0000-0000000000aa", **extra}
        return jwt.encode(claims, key, algorithm="RS256")
    scope, operator, email = verifier.verify_with_email(token(email=" Ann@Example.com "))
    assert (operator, email) == (False, "Ann@Example.com") and scope.owner_id == A
    assert verifier.verify_with_email(token(email="shy@example.com"))[1:] == (True, "shy@example.com")
    assert verifier.verify_with_email(token())[1:] == (False, None)
    assert verifier.verify_with_operator(token(email="shy@example.com"))[1] is True and len(verifier.verify_with_operator(token())) == 2


def test_the_account_menu_and_the_users_table_show_the_email(monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth
    monkeypatch.setattr(auth, "actor", lambda request, **kw: auth.Actor("u1", "Signed-In User", "ann@example.com", False))
    assert TestClient(module.app).get("/api/auth").json()["actor"] == "ann@example.com"
    monkeypatch.setattr(auth, "actor", lambda request, **kw: auth.Actor("u2", "Signed-In User", None, False))
    assert TestClient(module.app).get("/api/auth").json()["actor"] == "Signed-In User"
    ops_health._seen.clear()
    ops_health.touch("u1", "Signed-In User", "ann@example.com")
    assert [row["user"] for row in ops_health.sessions()] == ["ann@example.com"]
    ops_health._seen.clear()


# ---- 4. the log comes as a zip ----

def test_the_log_download_is_a_valid_zip_holding_the_unchanged_log():
    server_log_buffer.uninstall(); server_log_buffer._lines.clear(); server_log_buffer.install()
    try:
        import logging
        logging.getLogger("delivery_api.activity").info("Founder answer submitted for ann@example.com")
        name, body = ops_health.log_download(60)
        zip_name, data, lines = ops_health.log_zip(60)
    finally:
        server_log_buffer.uninstall(); server_log_buffer._lines.clear()
    assert zip_name.startswith("requi-server-log-") and zip_name.endswith(".zip") and zip_name[:-4] == name[:-4]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None and archive.namelist() == [name]
        text = archive.read(name).decode("utf-8")
    assert text.startswith("Requi.re server log") and "ann@example.com" not in text and "[email]" in text
    assert lines == text.count("\n")


def test_the_endpoint_serves_the_zip_to_an_admin_and_records_it(monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    module.app.dependency_overrides[auth.require_admin] = lambda: auth.Actor("admin", "Admin", "shy@example.com", True)
    try:
        response = TestClient(module.app).get("/api/admin/operations/log-download?minutes=15")
    finally:
        module.app.dependency_overrides.pop(auth.require_admin, None)
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert 'attachment; filename="requi-server-log-' in response.headers["content-disposition"] and response.headers["content-disposition"].endswith('.zip"')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None and len(archive.namelist()) == 1 and archive.namelist()[0].endswith(".txt")


# ---- 5. every table keeps its column headers ----

NODE_TABLE = r"""
class El{constructor(t){this.tag=t;this.kids=[];this.title='';this.className='';this.style={}}
 append(...k){for(const x of k)this.kids.push(typeof x==='string'?{text:x}:x)}
 replaceChildren(...k){this.kids=[];this.append(...k)}
 set textContent(v){this.kids=[{text:String(v)}];this._t=String(v)}
 get textContent(){return this._t||''}
 find(tag,out=[]){if(this.tag===tag)out.push(this);for(const k of this.kids)if(k.find)k.find(tag,out);return out}
 text(){return this.kids.map(k=>k instanceof El?k.text():k.text).join('')}}
globalThis.document={createElement:t=>new El(t)};
const {makeTable,el}=await import(%r);
const cols=[{key:'a',label:'Name',tip:'t'},{key:'b',label:'Amount',tip:'t',num:true}];
const root=new El('section');
const t=makeTable(root,'Title','tip',cols,'Nothing yet.');
const shape=()=>{const th=root.find('th').map(x=>x.text());const td=root.find('td').map(x=>x.text());return {th,td,tables:root.find('table').length,thead:root.find('thead').length}};
t.set([]);const empty=shape();
t.set([{a:'x',b:1}]);const full=shape();
console.log(JSON.stringify({empty,full}));
"""


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0, reason="needs node")
def test_an_empty_table_still_shows_its_columns_with_the_note_in_the_body():
    out = _node(NODE_TABLE % (WEB / "ops-table.js").as_uri())
    assert out["empty"]["tables"] == 1 and out["empty"]["thead"] == 1
    assert out["empty"]["th"] == ["Name", "Amount"] and out["empty"]["td"] == ["Nothing yet."]
    assert out["full"]["th"] == ["Name", "Amount"] and out["full"]["td"] == ["x", "1"]


def test_the_task_audit_table_keeps_its_columns_when_empty():
    source = (WEB / "taskactions.js").read_text()
    assert "colspan=\"4\">No matching actions.</td></tr>" in source and "data-audit-sort" in source


# ---- 6. one header, navigation and footer ----

def test_the_app_and_the_operator_page_share_the_same_header_and_footer():
    import re
    header = lambda text: re.search(r"<header>.*?</header>", text, re.S).group(0)
    index = (WEB / "index.html").read_text()
    ops = (WEB / "ops.html").read_text()
    for page in (index, ops):
        h = header(page)
        assert '<a class="brand" href="/">' in h and 'id="identity"' in h
        for label in ("Home", "All sessions", "Add Credits", "About"):
            assert ">%s</a>" % label in h
        assert "/assets/chrome.js" in page and "/assets/chrome.css" in page and "/assets/style.css" in page
    # the same brand block and navigation links, character for character
    cut = lambda h: re.sub(r'<span id="sessioncost".*?</span>', "", h, flags=re.S)
    assert cut(header(index)) == cut(header(ops))
    # the avatar menu on the operator page comes from the app's own identity code
    assert "ops-account.js" in ops and 'id="modal"' in ops
    assert "createIdentity" in (WEB / "ops-account.js").read_text()


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0, reason="needs node")
def test_the_operator_pages_are_navigation_items_for_administrators_only():
    out = _node(r"""
class A{constructor(){this.dataset={};this.attrs={};this.className='';this.title='';this.textContent='';this.href=''}setAttribute(k,v){this.attrs[k]=v}removeAttribute(k){delete this.attrs[k]}}
const made=[];const nav={links:[],append(a){this.links.push(a)},querySelector(s){return this.links.find(l=>l.dataset.operator)||null},};
globalThis.document={createElement:()=>{const a=new A();made.push(a);return a},querySelectorAll:()=>[],querySelector:()=>null};
globalThis.location={pathname:'/ops',hash:'#stops',href:'http://x/ops#stops'};
const {addOperatorLinks,OPERATOR_PAGES}=await import(%r);
const guest=addOperatorLinks(nav,{authenticated:true,admin:false});
const anon=addOperatorLinks(nav,{authenticated:false,admin:true});
const admin=addOperatorLinks(nav,{authenticated:true,admin:true});
const twice=addOperatorLinks(nav,{authenticated:true,admin:true});
console.log(JSON.stringify({guest,anon,admin,twice,links:nav.links.map(l=>[l.textContent,l.href])}));
""" % (WEB / "chrome.js").as_uri())
    assert out["guest"] is False and out["anon"] is False and out["admin"] is True and out["twice"] is False
    assert out["links"] == [["Server Health", "/ops#health"], ["Admin Stops", "/ops#stops"], ["Credits And Spend", "/ops#spend"], ["Grant Credits", "/ops#grant"]]
