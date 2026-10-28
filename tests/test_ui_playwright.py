"""Browser-level contracts for the three task views and traceable rerun UX."""
import tempfile
from pathlib import Path as _ScreenshotPath
SCREENSHOTS = _ScreenshotPath(tempfile.mkdtemp(prefix='delivery-browser-evidence-'))
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    db = root / "ui.db"
    artifacts=root/"artifacts";session=artifacts/"golden-1";(session/"interview").mkdir(parents=True);(session/"spec").mkdir()
    import json
    (session/"interview/intent_model.json").write_text(json.dumps({"problem_statement":"Family task evidence"}))
    partial=artifacts/"partial-interview";(partial/"interview").mkdir(parents=True)
    (partial/"interview/intent_model.json").write_text(json.dumps({"problem_statement":"Partial discovery evidence"}))
    live=artifacts/"live-interview";(live/"interview").mkdir(parents=True)
    (live/"interview/usage.jsonl").write_text('{}\n')
    (live/"interview/http-session.json").write_text(json.dumps({"status":30,"request":{"idea":"A founder brief for a live interview"}}))
    (session/"interview/audit.jsonl").write_text("\n".join([json.dumps({"event":"gate.auto_decision","gate":"concept","action":"select"}),json.dumps({"event":"interviewee.answer","answer":{"text":"Caregivers need a calm shared view","confidence":"high"}})])+"\n")
    (session/"spec/hands-free-run.json").write_text(json.dumps({"completed_at":"2026-09-19T08:00:00Z"}))
    (session/"spec/bundle-integrity.json").write_text(json.dumps({"files_checked":14,"diagnostics":[],"hashes":{"needs.json":"abc"}}))
    (session/"spec/needs.json").write_text(json.dumps({"needs":[{"need_id":"N-1","statement":"Families need visible task status"}]}))
    (session/"spec/srs.json").write_text(json.dumps({"functional":[{"requirement_id":"R-1","title":"Visible status","statement":"The system shall show status","need_ids":["N-1"],"acceptance":[{"criterion_id":"AC-1","statement":"Status is visible"}]}]}))
    (session/"spec/architecture.json").write_text(json.dumps({"decisions":[{"decision_id":"ADR-1","title":"Progressive web app","decision":"Build a PWA","rationale":"Browser-first scope","options_considered":[{"option":"Native","summary":"A dedicated mobile client","pros":["Deeper device access"],"cons":["Two codebases"],"why_rejected":"Outside first release"}]}],"components":[{"name":"Task service","responsibility":"Track work"}]}))
    (session/"spec/items.json").write_text(json.dumps([{"spec_id":"SPEC-1","title":"Task status module","requirement_ids":["R-1"]}]))
    (session/"spec/tests.json").write_text(json.dumps([{"test_id":"T-1","title":"Status visible","criterion_ids":["AC-1"],"spec_ids":["SPEC-1"]}]))
    (session/"spec/audit.jsonl").write_text(json.dumps({"event":"gate.simulated_expert_opinion","gate":"architecture","action":"approve","upstream_advisories":[{"artifact":"srs"}]})+"\n")
    env = {**os.environ, "DELIVERY_DB_PATH": str(db),"DELIVERY_DATABASE_URL": f"sqlite:///{db}","VMODEL_ARTIFACTS_DIR":str(artifacts),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    seed = '''
from delivery_api.store import SQLiteStore
import os
s=SQLiteStore(os.environ["DELIVERY_DB_PATH"])
s.create_task("Empty definition", "Awaiting its first run", task_id="empty")
s.create_task("Disposable definition", "Renamed and deleted by one test", task_id="disposable")
s.create_task("Live definition", "Detailed progress", task_id="live")
s.set_model_route("live", "ollama/phi4")
s.update_phase("live", "intent", "done", detail="Approved")
s.update_phase("live", "conops", "running", detail="Option 2/3", completed=2, total=3)
s.add_event("live", "progress", "Generating CONOPS option 2/3", phase_key="conops")
s.add_event("live", "price", "Price 1.25 credits", phase_key="intent", payload={"tokens":100000,"price":1.25,"price_usd":1.25,"rates":{"scale":"credits"}})
s.create_task("Finished definition", "Artifacts ready", task_id="done")
for key,_ in __import__("delivery_api.store", fromlist=["PHASES"]).PHASES: s.update_phase("done", key, "done")
s.add_task_artifact("done", "architecture", "Architecture overview", "/architecture.json")
s.create_task("Interactive simulation", "Recorded run, no process load", task_id="sim")
s.configure_simulation("sim", "/tmp/recorded", "ollama/phi4")
s.update_phase("sim", "intent", "done", detail="Recorded phase complete")
s.set_gate_open("sim", "gate-conops", True)
s.wait_at_gate("sim", "intent")
'''
    subprocess.run([sys.executable, "-c", seed], check=True, env=env)
    port = _port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "delivery_api.app:app", "--host", "127.0.0.1", "--port", str(port)], env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(base + "/health", timeout=.2); break
        except Exception: time.sleep(.1)
    else: raise RuntimeError("UI server did not start")
    yield base
    proc.terminate(); proc.wait(timeout=5)


@pytest.fixture()
def page(ui):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"] if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") else None)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        # Done tasks auto-open the completion screen once per session; other tests inspect the task page.
        page.add_init_script("if(!localStorage.getItem('allow-complete-redirect')){for(const id of ['done','live','sim'])sessionStorage.setItem('complete-shown:'+id,'1')}")
        yield page
        browser.close()

@pytest.fixture(scope="module", autouse=True)
def screenshot_directory(tmp_path_factory):
    global _screenshots_dir
    _screenshots_dir = tmp_path_factory.mktemp("ui-screenshots")



@pytest.fixture
def pg_ids(pg_browser):
    return {key:pg_browser[2].scope.owner_id+'-'+key for key in ('empty','disposable','live','done','sim')}


@pytest.fixture
def pg_ui(pg_browser,pg_ids):
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):
        s=workspace.store
        s.create_task("Empty definition", "Awaiting its first run", task_id=pg_ids["empty"])
        s.create_task("Disposable definition", "Renamed and deleted by one test", task_id=pg_ids["disposable"])
        s.create_task("Live definition", "Detailed progress", task_id=pg_ids["live"])
        s.set_model_route(pg_ids["live"], "ollama/phi4")
        s.update_phase(pg_ids["live"], "intent", "done", detail="Approved")
        s.update_phase(pg_ids["live"], "conops", "running", detail="Option 2/3", completed=2, total=3)
        s.add_event(pg_ids["live"], "progress", "Generating CONOPS option 2/3", phase_key="conops")
        s.add_event(pg_ids["live"], "price", "Price 1.25 credits", phase_key="intent", payload={"tokens":100000,"price":1.25,"price_usd":1.25,"rates":{"scale":"credits"}})
        s.create_task("Finished definition", "Artifacts ready", task_id=pg_ids["done"])
        for key,_ in __import__("delivery_api.store", fromlist=["PHASES"]).PHASES: s.update_phase(pg_ids["done"], key, "done")
        s.add_task_artifact(pg_ids["done"], "architecture", "Architecture overview", "/architecture.json")
        s.create_task("Interactive simulation", "Recorded run, no process load", task_id=pg_ids["sim"])
        s.configure_simulation(pg_ids["sim"], "/tmp/recorded", "ollama/phi4")
        s.update_phase(pg_ids["sim"], "intent", "done", detail="Recorded phase complete")
        s.set_gate_open(pg_ids["sim"], "gate-conops", True)
        s.wait_at_gate(pg_ids["sim"], "intent")
    page.add_init_script('for(const id of '+json.dumps(list(pg_ids.values()))+")sessionStorage.setItem('complete-shown:'+id,'1')")
    return base


@pytest.fixture
def pg_page(pg_ui,pg_browser):
    return pg_browser[0]

def _promote_control(snapshot):
    legacy=snapshot.get('ui_state') or {}
    c=snapshot.setdefault('control_snapshot',{})
    mode={'engine_question':'waiting_input','engine_decision':'waiting_input','phase_gate':'waiting_gate','executing':'working','complete':'completed','error':'failed','not_started':'not_started'}
    c['version']=2
    if legacy.get('mode'):c['mode']=mode[legacy['mode']]
    c['phase_key']=snapshot.get('active_phase')
    if 'process_live' in legacy:c['process_live']=legacy['process_live']
    c.setdefault('kind','text' if legacy.get('mode')=='engine_question' else 'choice' if legacy.get('mode')=='engine_decision' else None)
    if 'decision' in legacy:c['decision']=legacy['decision']
    if 'gate' in legacy:c['gate']=legacy['gate']
    if 'question' in legacy:c['prompt']=legacy['question']
    if 'input_id' in legacy:c['input_id']=legacy['input_id']
    if 'diagnostic' in legacy:c['diagnostic']=legacy['diagnostic']
    snapshot.pop('ui_state',None)
    return snapshot

def route_task_snapshot(page,ui,task_id,mutate):
    snapshot=page.request.get(f'{ui}/api/tasks/{task_id}').json();mutate(snapshot)
    _promote_control(snapshot)
    page.route(f'**/api/tasks/{task_id}',lambda route:route.fulfill(json=_promote_control(snapshot)))
    return snapshot

def mock_gate_review(page,ui,task_id,semantic=None):
    # Current contract snapshots artifact content together with the gate revision.
    # Reuse test-owned task/artifact mocks rather than fall through to real files.
    def review(route):
        import urllib.parse
        request=urllib.parse.urlparse(route.request.url)
        gate_key=request.path.split('/')[-2]
        task=page.evaluate("async url=>(await fetch(url)).json()",f'/api/tasks/{task_id}')
        gate=next(g for g in task['gates'] if g['gate_key']==gate_key)
        documents=[]
        for a in task.get('artifacts',[]):
            if a['phase_key']==gate['after_phase'] and not a.get('stale'):
                view=page.evaluate("async url=>(await fetch(url)).json()",f"/api/tasks/{task_id}/artifacts/{a['phase_key']}/{a['name']}/view")
                documents.append({'name':a['name'],'view':view})
        route.fulfill(json={'review':{**({'semantic_review':semantic} if semantic else {}),'revision':'gate:'+__import__('hashlib').sha256(json.dumps({'gate':gate,'artifacts':task.get('artifacts',[]),'documents':documents},sort_keys=True).encode()).hexdigest(),'task':task,'documents':documents},'actionable':gate['state']=='waiting','comments':[],'draft':None})
    page.route(f'**/api/tasks/{task_id}/gates/*/review**',review)

def waiting_gate_snapshot(snapshot):
    gate=snapshot['gates'][0];gate.update({'state':'waiting','is_open':0})
    for other in snapshot['gates'][1:]:other['state']='idle'
    snapshot.update({'state':'waiting_gate','active_phase':gate['after_phase']})
    for phase in snapshot['phases']:
        if phase['phase_key']==gate['after_phase']:phase.update({'state':'awaiting_approval','detail':'Generated; waiting for founder approval'})
    snapshot['control_snapshot']={'version':2,'mode':'waiting_gate','owner':'delivery','wait_id':gate['gate_key'],'gate':gate,'phase_key':gate['after_phase'],'process_live':False}
    snapshot['ui_state']={'mode':'phase_gate','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':gate}


def test_dashboard_draws_v_and_all_task_states(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    page.goto(ui)
    page.locator(".task").first.wait_for()
    assert page.locator(".task").count() == 5
    assert page.locator(".v-map svg").count() == 5
    # Home is task-only; the old session pages are gone.
    assert page.locator('.hero,.home-sessions,.session-card').count()==0
    assert page.get_by_text("In progress").count() == 1
    assert page.get_by_text("Complete").count() == 1


@pytest.mark.ui_cosmetic
def test_require_brand_is_consistent(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    page.goto(ui)
    assert page.title() == "Requi.re / Product definition workspace"
    brand=page.locator('.brand-name')
    assert brand.inner_text().startswith('Requi.re')
    assert brand.locator('strong').inner_text()=='.re'
    assert page.locator('body').inner_text().lower().find('vector') == -1
    page.goto(ui+'/about')
    page.get_by_text('About Requi.re',exact=True).wait_for();assert page.get_by_text('About Requi.re',exact=True).is_visible()
    assert page.locator('body').inner_text().lower().find('vector') == -1


def test_not_started_panel_layout(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    page.goto(ui+f'/tasks/{pg_ids["empty"]}/view')
    page.get_by_role('group',name='Default model route').wait_for()
    start=page.get_by_role('button',name='Start interview')
    panel=page.locator('.not-started');route=panel.get_by_role('group',name='Default model route');label=panel.locator('.start-route');
    assert panel.evaluate("e=>getComputedStyle(e).borderRadius")=='28px'
    assert panel.locator('.start-controls').count()==1
    assert label.inner_text().startswith('DEFAULT MODEL ROUTE')
    control=route.bounding_box();action=start.bounding_box();assert control['y']+control['height']+12<action['y']
    assert control['y']>=label.bounding_box()['y']+12
    assert start.is_visible() and not start.is_enabled()
    assert 'cannot start:' in panel.locator('#startstatus').inner_text()


def test_start_screen_displays_engine_resolved_model_and_conflict(page, ui):
    routes={'routes':[{'id':'ollama','available':True,'model':'ollama:phi4'},
                      {'id':'deepseek','available':False,'error':"selected route 'deepseek' conflicts with INTERVIEW_ENGINE_MODEL_PROBING='ollama:phi4'"}]}
    page.route('**/api/model-routes',lambda route:route.fulfill(json=routes))
    page.goto(ui+'/tasks/empty/view')
    selector=page.get_by_role('group',name='Default model route');selector.wait_for()
    assert 'ollama:phi4' in selector.locator('[data-start-route=ollama]').inner_text()
    assert selector.locator('[data-start-route=deepseek]').is_disabled()
    assert 'INTERVIEW_ENGINE_MODEL_PROBING' in selector.inner_text()
    assert selector.locator('[data-start-route=ollama]').get_attribute('aria-pressed')=='true'
    selector.locator('[data-start-route=ollama]').click()
    assert page.get_by_role('button',name='Start interview').is_enabled()
    page.screenshot(path=str(_screenshots_dir / "interview-model-preview.png"),full_page=True)


def test_not_started_running_and_done_pages(page, ui):
    page.goto(ui + "/tasks/empty/view")
    page.get_by_text("This task hasn't started.").wait_for();assert page.get_by_text("This task hasn't started.").is_visible()
    start=page.get_by_role("button",name="Start interview");assert start.is_visible() and not start.is_enabled()
    assert page.get_by_text("Start engine · wiring next").count()==0
    page.goto(ui + "/tasks/live/view")
    page.locator(".live-indicator").wait_for()
    assert page.get_by_text("Live engine output").count() == 0
    assert page.locator("#console").count() == 0 and page.locator("#progressfeed").count() == 0
    page.locator(".avatar").click();page.locator("#opendebug").click()
    page.locator(".debug-dialog").wait_for();assert page.locator("#console").count()==0
    page.get_by_role("button",name="Close debug").click()
    page.goto(ui + "/tasks/done/view")
    page.get_by_text("Artifacts", exact=True).wait_for();assert page.get_by_text("Artifacts", exact=True).is_visible()
    assert page.get_by_role("button",name="Architecture overview").is_visible()


def test_rerun_preview_explains_archive_and_audit_has_local_actor(page, ui):
    page.goto(ui + "/tasks/done/view")
    page.get_by_role("button", name="Rerun stage").click()
    assert page.get_by_text("Downstream artifacts will turn stale immediately", exact=False).is_visible()
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Audit").click()
    page.get_by_text("Task audit").wait_for()
    assert "local user" in page.locator(".dialog").inner_text()


def test_gate_open_waiting_halo_and_full_page(page, ui):
    route_task_snapshot(page,ui,'sim',waiting_gate_snapshot)
    page.goto(ui + "/tasks/sim/view")
    waiting=page.locator('#openphasegate');waiting.wait_for()
    assert waiting.count()==1 and waiting.is_visible()
    awaiting=page.locator('.phase.awaiting_approval');assert awaiting.count()==1
    palette=awaiting.locator('.dot').evaluate("e=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopColor})")
    assert palette['background']=='rgb(229, 182, 61)' and palette['border']=='rgb(229, 182, 61)'
    waiting.click();page.locator("#gateform").wait_for()
    assert "/tasks/sim/gates/" in page.url
    assert page.get_by_text("Waiting here · approval paused").is_visible()
    assert page.get_by_text("actor: local user", exact=False).count()==0  # actor line removed from gate header (debug data)
    assert page.get_by_role("button", name="Ask for changes").is_visible()

def test_gate_redo_requires_steer_in_browser(page, ui):
    route_task_snapshot(page,ui,'sim',waiting_gate_snapshot)
    page.route('**/api/tasks/sim/gates/*/decide',lambda route:route.fulfill(status=409,json={'detail':{'error':'a redo needs a reason','cause':'Redo without a steer is a reroll.','next':'Describe what should change.'}}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui + "/tasks/sim/gates/gate-intent");page.locator('#gateform').wait_for()
    page.get_by_role("button", name="Ask for changes").click()
    page.get_by_text("a redo needs a reason", exact=False).wait_for()

def test_an_unavailable_artifact_shows_a_notice_instead_of_a_dead_click(page, ui):
    page.route('**/api/tasks/done/artifacts/**/view', lambda route: route.fulfill(status=404, json={'detail': 'Artifact files are not part of a local preview'}))
    page.goto(ui + '/tasks/done/view')
    button = page.locator('[data-view-artifact]').first
    button.wait_for()
    button.click()
    dialog = page.locator('.dialog', has_text='not part of a local preview')
    dialog.wait_for()
    assert 'Artifact files are not part of a local preview' in dialog.inner_text()


def test_basic_auth_browser_signup_signin_persists_and_logs(tmp_path):
    db=tmp_path/"basic-ui.db";port=_port();log=Path(__file__).parents[1]/"run.log"
    env={**os.environ,"DELIVERY_DB_PATH":str(db),"DELIVERY_DATABASE_URL":f"sqlite:///{db}","DELIVERY_BASIC_AUTH_ENABLED":"1","DELIVERY_COOKIE_SECURE":"0","PYTHONPATH":str(Path(__file__).parents[1]/"src")}
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","delivery_api.app:app","--host","127.0.0.1","--port",str(port)],env=env);base=f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:import urllib.request;urllib.request.urlopen(base+"/health",timeout=.2);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(base+"/")
            page.get_by_text("Welcome to Requi.re",exact=True).wait_for()
            page.evaluate("window.__guardToken=Math.random()")
            # clicking the link to the page you are already on must not reload it (the token survives and the address is unchanged)
            page.get_by_role("link",name="Home",exact=True).click()
            assert page.evaluate("window.__guardToken") is not None and page.url==base+"/"
            page.get_by_role("button",name="Create account").click()
            email=page.locator('input[name="username"]');email.fill('partial');assert page.locator('#emailstatus').inner_text()==''
            email.blur();assert page.locator('#emailstatus').inner_text()=='Enter a complete email address.'
            email.fill('shy@example.com');page.get_by_text('Email is available.').wait_for()
            page.locator('input[name="password"]').fill('Strong local 42!');page.locator('.auth-submit').click()
            page.locator('.avatar').wait_for();assert page.url==base+'/'
            assert page.locator('.avatar').inner_text()=='SH'
            page.reload();page.locator('.avatar').wait_for();assert page.locator('.avatar').is_visible()
            page.goto(base+'/sessions');page.wait_for_url(base+'/');page.locator('.avatar').wait_for()
            browser.close()
        assert 'auth.signup.success' in log.read_text() and 'client.navigation' in log.read_text()
    finally:proc.terminate();proc.wait(timeout=5)

def test_boot_smoke_zero_console_errors_anonymous_and_after_fake_login(tmp_path):
    # The browser-level boot gate: the page must leave "Loading workspace\u2026" with
    # ZERO console/page errors, anonymous AND signed in (identity.js shipped a syntax
    # error every python gate missed - PR #261). The fake account lives only in this
    # test's own sqlite file; no live database is ever touched.
    db=tmp_path/"boot-smoke.db";port=_port()
    env={**os.environ,"DELIVERY_DB_PATH":str(db),"DELIVERY_DATABASE_URL":f"sqlite:///{db}","DELIVERY_BASIC_AUTH_ENABLED":"1","DELIVERY_COOKIE_SECURE":"0","PYTHONPATH":str(Path(__file__).parents[1]/"src")}
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","delivery_api.app:app","--host","127.0.0.1","--port",str(port)],env=env);base=f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:import urllib.request;urllib.request.urlopen(base+"/health",timeout=.2);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page()
            errors=[]
            page.on('console',lambda m:errors.append('console: '+m.text) if m.type=='error' else None)
            page.on('pageerror',lambda e:errors.append('pageerror: '+str(e)))
            page.goto(base+"/")
            page.wait_for_function("!document.body.innerText.includes('Loading workspace')")
            page.get_by_text("Welcome to Requi.re",exact=True).wait_for()
            assert errors==[],f"anonymous boot produced browser errors: {errors}"
            # fake login through the real UI - the account exists only in this test's DB
            page.get_by_role("button",name="Create account").click()
            page.locator('input[name="username"]').fill('boot.smoke@example.test');page.locator('input[name="password"]').fill('Boot smoke 42!');page.locator('.auth-submit').click()
            page.locator('.avatar').wait_for()
            page.reload();page.locator('.avatar').wait_for()
            page.wait_for_function("!document.body.innerText.includes('Loading workspace')")
            assert page.get_by_role('button',name='+ New task').is_visible()
            assert errors==[],f"signed-in boot produced browser errors: {errors}"
            browser.close()
    finally:proc.terminate();proc.wait(timeout=5)

def test_oauth_identity_and_logout_in_browser(tmp_path):
    db=tmp_path/"oauth-ui.db";port=_port()
    env={**os.environ,"DELIVERY_DB_PATH":str(db),"DELIVERY_DATABASE_URL":f"sqlite:///{db}","DELIVERY_SESSION_SECRET":"0123456789abcdef0123456789abcdef",
         "DELIVERY_GOOGLE_CLIENT_ID":"id","DELIVERY_GOOGLE_CLIENT_SECRET":"secret",
         "DELIVERY_COOKIE_SECURE":"0","PYTHONPATH":str(Path(__file__).parents[1]/"src")}
    seed='''from delivery_api.store import SQLiteStore\nimport os,time\ns=SQLiteStore(os.environ["DELIVERY_DB_PATH"])\nu=s.upsert_user("google","42","shy@example.com","shy@example.com",1000,True)\ns.create_session("browser-session",u["user_id"],time.time()+1000)'''
    subprocess.run([sys.executable,"-c",seed],check=True,env=env)
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","delivery_api.app:app","--host","127.0.0.1","--port",str(port)],env=env)
    base=f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                import urllib.request;urllib.request.urlopen(base+"/health",timeout=.2);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);context=browser.new_context()
            context.add_cookies([{"name":"vmodel_session","value":"browser-session","url":base}])
            page=context.new_page();page.goto(base)
            page.get_by_role("button",name="Account menu for shy@example.com").click()
            page.get_by_role("menuitem",name="Sign out").click()
            page.get_by_text("Welcome to Requi.re",exact=True).wait_for()
            assert page.get_by_role("button",name="Sign in").first.is_visible()
            browser.close()
    finally:
        proc.terminate();proc.wait(timeout=5)


def test_task_filter_sort_rename_and_delete(page,ui):
    page.goto(ui);page.locator('.task').first.wait_for()
    page.locator('#taskfilter').fill('Finished definition')
    assert page.locator('.task').count()==1
    page.locator('#taskfilter').fill('')
    page.locator('#tasksort').select_option('name-asc')
    names=page.locator('.task h2').all_text_contents()
    assert names==sorted(names)
    card=page.locator('.task',has_text='Disposable definition')
    card.get_by_role('button',name='Rename').click()
    page.locator('#renameform input[name=title]').fill('Renamed disposable definition')
    page.get_by_role('button',name='Save name').click()
    page.get_by_text('Renamed disposable definition',exact=True).wait_for()
    card=page.locator('.task',has_text='Renamed disposable definition')
    card.get_by_role('button',name='Delete').click()
    assert page.get_by_text('Original files in the artifacts folder stay on disk.').is_visible()
    page.get_by_role('button',name='Delete task').click()
    page.locator('#app').get_by_text('Renamed disposable definition',exact=True).wait_for(state='detached')
    assert page.get_by_text('Renamed disposable definition',exact=True).count()==0


def test_dashboard_routes_local_loading_to_session_folder_not_file_picker(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    page.goto(ui)
    page.get_by_role("navigation").get_by_role("link",name="Add Credits").wait_for();assert page.get_by_role("link",name="All sessions").count()==0
    assert page.get_by_role("button",name="Import run").count()==0
    for old in ("/sessions","/sessions/golden-1/view","/sessions/golden-1/needs"):
        page.goto(ui+old);page.locator(".task").first.wait_for();assert page.url==ui+"/" or page.url==ui
    assert page.get_by_role("button",name="Artifacts folder").count()==0

def test_page_headers_stay_sticky_and_home_has_no_evidence_vault_hero(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    page.goto(ui+"/")
    page.locator(".task").first.wait_for()
    assert page.get_by_text("Evidence vault / completed sessions",exact=True).count()==0
    assert page.get_by_text("Every decision,",exact=False).count()==0
    page.goto(ui+f'/tasks/{pg_ids["done"]}/view')
    head=page.locator('.detail-head.page-head');head.wait_for()
    assert head.evaluate("e=>getComputedStyle(e).position")=="sticky"

def test_create_task_dialog_keyboard_submit_textarea_and_cancel(page,ui):
    page.goto(ui)
    page.get_by_role('button',name='+ New task').click()
    title=page.locator('#newform input[name="title"]')
    page.wait_for_function("document.activeElement?.name==='title'");assert title.evaluate('e=>document.activeElement===e')
    title.fill('Keyboard-created task');title.press('Enter')
    page.get_by_text("This task hasn't started.").wait_for()
    assert page.get_by_role('heading',name='Keyboard-created task').is_visible()

    page.goto(ui);page.get_by_role('button',name='+ New task').click()
    title=page.locator('#newform input[name="title"]');brief=page.locator('#newform textarea[name="brief"]')
    title.fill('Textarea keyboard task');brief.fill('Line one');brief.press('Enter');brief.fill('Line one\nLine two')
    assert brief.input_value()=='Line one\nLine two' and page.locator('#newform').is_visible()
    brief.press('Control+Enter')
    page.get_by_text("This task hasn't started.").wait_for()
    assert page.get_by_role('heading',name='Textarea keyboard task').is_visible()

    page.goto(ui);page.locator('.task').first.wait_for();before=page.locator('.task').count();page.get_by_role('button',name='+ New task').click()
    page.locator('#newform input[name="title"]').fill('Must not be created');page.keyboard.press('Escape')
    assert page.locator('#newform').count()==0 and page.locator('.task').count()==before


def test_create_task_button_disables_until_the_server_returns(page,ui):
    page.goto(ui)
    page.get_by_role('button',name='+ New task').click()
    page.locator('#newform input[name="title"]').fill('Guarded create')
    held=[]
    def guard(route):
        if route.request.method=='POST':held.append(route)
        else:route.continue_()
    page.route('**/api/tasks',guard)
    create=page.locator('#newform button.primary')
    create.click()
    # A fast second click while the call is in flight is a no-op on the disabled
    # button: exactly one POST may reach the server.
    create.evaluate('e=>e.click()')
    assert create.is_disabled() and create.inner_text()=='Creating\u2026'
    assert create.get_attribute('aria-busy')=='true' and create.get_attribute('title')=='Creating the task - one moment'
    assert len(held)==1
    page.screenshot(path=str(_screenshots_dir / 'create-task-button-busy.png'),full_page=False)
    held[0].fulfill(json={'task_id':'guard-created'})
    page.wait_for_url('**/tasks/guard-created/view')


def test_start_interview_button_disables_until_the_server_returns(page,ui):
    routes={'routes':[{'id':'ollama','available':True,'model':'ollama:phi4'}]}
    page.route('**/api/model-routes',lambda route:route.fulfill(json=routes))
    held=[]
    page.route('**/api/tasks/empty/start',lambda route:held.append(route))
    page.goto(ui+'/tasks/empty/view')
    start=page.locator('#startengine');start.wait_for()
    assert start.inner_text()=='Start interview'
    start.click()
    start.evaluate('e=>e.click()')
    assert start.is_disabled() and start.inner_text()=='Starting interview\u2026'
    assert start.get_attribute('aria-busy')=='true' and start.get_attribute('title')=='Starting the interview - one moment'
    assert len(held)==1
    page.screenshot(path=str(_screenshots_dir / 'start-interview-button-busy.png'),full_page=False)
    held[0].fulfill(json={'session_id':'guard-session'})
    # The acknowledged start re-renders the task page from the server snapshot;
    # the re-rendered button is usable again.
    page.wait_for_function("document.querySelector('#startengine')&&!document.querySelector('#startengine').disabled")


def test_confirm_rerun_button_disables_until_the_server_returns(page,ui):
    page.goto(ui+'/tasks/done/view');page.get_by_role('button',name='Rerun stage').click()
    page.locator('#rerunform').wait_for()
    held=[]
    def guard(route):
        if (route.request.post_data_json or {}).get('confirm'):held.append(route)
        else:route.fulfill(json={'downstream':['architecture','specs','tests'],'message':'Earlier generation remains archived.'})
    page.route('**/api/tasks/done/rerun',guard)
    page.get_by_role('button',name='Review impact').click()
    confirm=page.get_by_role('dialog',name='Confirm rerun');confirm.wait_for()
    button=confirm.locator('#confirm')
    assert button.inner_text()=='Confirm rerun'
    button.click()
    button.evaluate('e=>e.click()')
    assert button.is_disabled() and button.inner_text()=='Starting rerun\u2026'
    assert button.get_attribute('aria-busy')=='true' and button.get_attribute('title')=='Starting the rerun - one moment'
    assert len(held)==1
    page.screenshot(path=str(_screenshots_dir / 'confirm-rerun-button-busy.png'),full_page=False)
    held[0].fulfill(json={'ok':True})
    confirm.wait_for(state='detached')


def test_user_progress_and_composer_have_no_raw_console(page,ui):
    page.add_init_script("class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'test-input','precondition':{'session_sequence':1}}
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'decision':None,'gate':None,'question':'Who uses it?'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view');page.locator('#answerform').wait_for()
    assert page.locator('#console').count()==0 and page.locator('.execution-data').count()==0
    page.locator('.avatar').click();page.locator('#opendebug').click();page.locator('.debug-dialog').wait_for()
    assert page.locator('#console').count()==0
    page.get_by_role('button',name='Close debug').click()
    assert page.locator('#answerform').is_visible()
    page.evaluate("liveSource.emit({id:999999,kind:'progress',message:'Still working',phase_key:'intent',payload:{status:'heartbeat',stage:'intent'},ts:1720000200})")
    # Waiting-for-input is authoritative over a late heartbeat; history remains inspectable.
    expect(page.locator('#stageprogress .progress-current')).to_have_text('Waiting for your answer')
    page.locator('#stageprogress > details > summary').click()
    assert page.locator('#stageprogress').get_by_text('Still working',exact=False).is_visible()


def test_answer_failure_preserves_text_and_recovers_button(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'test-input','precondition':{'session_sequence':1}}
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'decision':None,'gate':None,'question':'What matters most?'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/live/answer',lambda route:route.fulfill(status=503,json={'detail':{'error':'Answer could not be sent.','cause':'Engine input is unavailable.','next':'Retry when the engine is waiting.'}}))
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea');button=page.locator('#answerform button[type=submit]')
    answer.fill('Do not lose this answer');button.click()
    page.get_by_text('Answer could not be sent.',exact=False).wait_for()
    assert answer.input_value()=='Do not lose this answer' and answer.is_enabled() and button.is_enabled() and button.inner_text()=='Send answer'
    assert page.locator('#answerspinner').is_hidden()

def test_rerun_review_opens_top_dialog_and_escape_restores_flow(page,ui):
    page.goto(ui+'/tasks/done/view');page.get_by_role('button',name='Rerun stage').click()
    form=page.locator('#rerunform');form.wait_for();selected=form.locator('select').input_value()
    page.route('**/api/tasks/done/rerun',lambda route:route.fulfill(json={'downstream':['architecture','specs','tests'],'message':'Earlier generation remains archived.'}))
    page.get_by_role('button',name='Review impact').click()
    confirm=page.get_by_role('dialog',name='Confirm rerun');confirm.wait_for()
    assert confirm.is_visible() and confirm.get_by_role('button',name='Confirm rerun').evaluate('e=>document.activeElement===e')
    assert form.is_visible() and form.locator('select').input_value()==selected
    # The nested layer is visually above the preserved flow dialog.
    assert confirm.evaluate("e=>Number(getComputedStyle(e.closest('.dialog-layer')).zIndex)") >= 1
    page.keyboard.press('Escape');confirm.wait_for(state='detached')
    assert form.is_visible() and form.locator('select').input_value()==selected
    assert form.locator('select').evaluate('e=>document.activeElement===e')

def test_slow_answer_has_no_artificial_timeout_and_can_be_cancelled(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'test-input','precondition':{'session_sequence':1}}
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'decision':None,'gate':None,'question':'What matters most?'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    pending=[]
    def hold(route):pending.append(route)
    page.route('**/api/tasks/live/answer',hold)
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea');send=page.locator('#answerform button[type=submit]');cancel=page.get_by_role('button',name='Cancel send')
    answer.fill('A careful answer still in flight');send.click();cancel.wait_for()
    page.wait_for_timeout(16000)
    spinner=page.locator('#answerspinner')
    assert send.inner_text()=='Sending…' and send.is_disabled() and answer.is_disabled() and cancel.is_visible()
    assert spinner.is_visible() and spinner.get_attribute('role')=='status'
    assert spinner.locator('i').evaluate('e=>getComputedStyle(e).animationName')=='answer-spin'
    page.screenshot(path=str(_screenshots_dir / 'interview-answer-sending.png'),full_page=False)
    assert answer.input_value()=='A careful answer still in flight' and page.get_by_text('request timed out',exact=False).count()==0
    cancel.click();page.get_by_text('Send cancelled. Your answer is still here.').wait_for()
    assert send.is_enabled() and answer.is_enabled() and answer.input_value()=='A careful answer still in flight'
    assert spinner.is_hidden()
    if pending:pending[0].abort()

def test_answer_enter_sends_and_control_enter_inserts_newline(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'test-input','precondition':{'session_sequence':1}}
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'decision':None,'gate':None,'question':'What matters most?'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    sent=[]
    page.route('**/api/tasks/live/answer',lambda route:(sent.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea')
    answer.fill('Line one');answer.press('Control+Enter');answer.type('Line two')
    assert answer.input_value()=='Line one\nLine two' and sent==[]
    answer.press('Enter');page.get_by_text('Answer sent.',exact=False).wait_for()
    assert sent==[{'answer':'Line one\nLine two','decision_id':'test-input','expected_sequence':1}]

def test_structured_founder_decision_opens_large_card_dialog_and_formats_answer(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[];live['active_phase']='conops'
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    submitted=[]
    page.route('**/api/tasks/live/answer',lambda route:(submitted.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    decision={'type':'founder_decision','decision_id':'concept','title':'Choose the operating concept','prompt':'Which concept should the architecture execute?','rationale_required':True,'rationale_label':'Why is this right?','answer_format':'<value> - <rationale>','options':[{'value':'1','label':'Native app','summary':'Installed experience','advantages':['Fast offline'],'disadvantages':['App stores']},{'value':'2','label':'PWA','summary':'Browser first','advantages':['One deploy'],'disadvantages':['Limited APIs']}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':decision}
    live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice','wait_id':'concept','precondition':{'session_sequence':1}}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"conops",payload:{waiting_input:true,question:decision.prompt,decision},ts:1720000300})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.is_visible()
    assert dialog.get_by_text('Native app',exact=True).is_visible() and dialog.get_by_text('PWA',exact=True).is_visible()
    assert dialog.get_by_text('Fast offline',exact=False).is_visible() and dialog.get_by_text('Limited APIs',exact=False).is_visible()
    box=dialog.bounding_box();assert box['width']>900
    dialog.get_by_text('PWA',exact=True).click();dialog.locator('textarea').fill('One deployment fits our team')
    page.screenshot(path=str(_screenshots_dir / 'structured-founder-decision.png'),full_page=True)
    dialog.locator('textarea').press('Enter')
    assert submitted==[{'answer':'2 - One deployment fits our team','decision_id':'concept','expected_sequence':1}]


def test_structured_founder_decision_escape_closes_without_answer(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[]
    live['control_snapshot']={'mode':'working'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    decision={'type':'founder_decision','title':'Choose','prompt':'Pick','options':[{'value':'1','label':'A','summary':'First'},{'value':'2','label':'B','summary':'Second'}]}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:"Pick",phase_key:"conops",payload:{waiting_input:true,decision},ts:1720000300})',decision)
    page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape');assert page.locator('.decision-dialog').count()==0
    assert page.locator('#opendecision').is_visible()

def test_stored_pending_decision_reopens_on_page_load_and_panels_align(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='conops'
    decision={'type':'founder_decision','decision_id':'concept','title':'Choose after restart','prompt':'Which concept?','options':[{'value':'1','label':'Native','summary':'Installed'},{'value':'2','label':'PWA','summary':'Browser'}]}
    live['events']=[{'id':900,'kind':'founder_decision','message':'Which concept?','phase_key':'conops','payload':{'waiting_input':True,'question':'Which concept?','decision':decision},'ts':1720000300},{'id':901,'kind':'waiting_input','message':'You:','phase_key':'conops','payload':{'waiting_input':True,'question':'Which concept?'},'ts':1720000301}]
    live['ui_state']={'mode':'engine_decision','active_phase':live['active_phase'],'process_live':True,'decision':decision,'gate':None}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.set_viewport_size({'width':1365,'height':900});page.goto(ui+'/tasks/live/view')
    page.locator('.decision-dialog').wait_for();assert page.get_by_role('heading',name='Choose after restart').is_visible()
    page.keyboard.press('Escape');assert page.locator('#opendecision').is_visible()
    panels=page.locator('.detail-layout>.panel, .detail-layout>div>.panel');assert panels.count()==2
    boxes=[panels.nth(i).bounding_box() for i in range(2)]
    assert abs((boxes[0]['y']+boxes[0]['height'])-(boxes[1]['y']+boxes[1]['height']))<2
    assert page.locator('.live-status-row').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'restart-decision-aligned-panels.png'),full_page=True)

def test_detail_layout_never_crushes_or_overlaps_at_user_viewport(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='conops';long='Reply with the option number or exact concept name, then ` - ` and why you chose it. This is an explicit founder decision.'
    decision={'type':'founder_decision','title':'Choose the operating concept','prompt':long,'options':[{'value':'1','label':'Native','summary':'Installed'},{'value':'2','label':'PWA','summary':'Browser'}]}
    live['events']=[{'id':1,'kind':'founder_decision','message':long,'phase_key':'conops','payload':{'waiting_input':True,'question':long,'decision':decision},'ts':1720000300}]
    # Doctrine colors: approved green, generated yellow, active blue, pending gray.
    states=['approved','awaiting_approval','running','pending','pending','pending','pending']
    for phase,state in zip(live['phases'],states):phase['state']=state;phase['detail']='Waiting for founder approval' if state=='awaiting_approval' else ''
    live['ui_state']={'mode':'engine_decision','active_phase':live['active_phase'],'process_live':True,'decision':decision,'gate':None}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.set_viewport_size({'width':1390,'height':827});page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape')
    left=page.locator('.detail-layout>.panel');right=page.locator('.detail-layout>div>.panel')
    lb,rb=left.bounding_box(),right.bounding_box();assert abs((lb['y']+lb['height'])-(rb['y']+rb['height']))<2
    indicator=page.locator('.live-indicator');assert indicator.evaluate('e=>e.scrollWidth<=e.clientWidth')
    question=page.locator('#currentquestion p');assert question.evaluate('e=>e.scrollHeight<=e.clientHeight')
    progress=page.locator('.live-progress');assert progress.bounding_box()['x']>=right.bounding_box()['x']
    palette={s:page.locator(f'.phase.{s} .dot').first.evaluate('e=>getComputedStyle(e).backgroundColor') for s in states}
    assert palette['approved']=='rgb(22, 163, 106)' and palette['awaiting_approval']=='rgb(229, 182, 61)' and palette['running']=='rgb(78, 156, 247)' and palette['pending']=='rgb(242, 240, 233)'
    assert page.locator('.phase.running').count()==1
    assert page.locator('.live-console').count()==0
    page.screenshot(path=str(_screenshots_dir / 'detail-layout-clean-1390x827.png'),full_page=True)

def test_founder_dialog_fixed_controls_and_vertical_four_options(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[]
    live['control_snapshot']={'mode':'working'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.route('**/api/tasks/live/answer',lambda route:route.fulfill(json={'status':'accepted'}))
    page.set_viewport_size({'width':1390,'height':827});page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    options=[{'value':str(i),'label':name,'summary':'A deliberately detailed option summary.','advantages':['Clear advantage'],'disadvantages':['Known tradeoff']} for i,name in enumerate(['Native mobile','Progressive web app','Responsive web','Hybrid shell'],1)]
    decision={'type':'founder_decision','title':'Choose the operating concept','prompt':'Which concept should the architecture execute?','rationale_label':'Why is this the right concept?','options':options}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"conops",payload:{waiting_input:true,decision},ts:1720000300})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();cards=dialog.locator('.decision-option');assert cards.count()==4
    boxes=[cards.nth(i).bounding_box() for i in range(4)];assert all(boxes[i+1]['y']>boxes[i]['y']+boxes[i]['height']-1 for i in range(3))
    head=dialog.locator('.decision-head');compose=dialog.locator('.decision-compose');scroll=dialog.locator('.decision-scroll')
    assert head.bounding_box()['y']<compose.bounding_box()['y']<scroll.bounding_box()['y']
    assert dialog.locator('.decision-compose textarea').is_visible() and dialog.locator('.decision-compose button[type=submit]').is_visible()
    before=(head.bounding_box()['y'],compose.bounding_box()['y']);scroll.evaluate('e=>e.scrollTop=e.scrollHeight');after=(head.bounding_box()['y'],compose.bounding_box()['y']);assert before==after
    title=dialog.get_by_role('heading',name='Choose the operating concept');tb=title.bounding_box();hb=head.bounding_box();assert title.is_visible() and tb['y']>=hb['y'] and tb['y']+tb['height']<=hb['y']+hb['height']
    assert title.evaluate("e=>({opacity:getComputedStyle(e).opacity,position:getComputedStyle(e.closest('.decision-head')).position})")=={'opacity':'1','position':'relative'}
    cards.nth(2).click();selected=cards.nth(2);assert selected.evaluate("e=>e.classList.contains('selected')")
    style=selected.locator('.option-copy b').evaluate("e=>({weight:getComputedStyle(e).fontWeight,bg:getComputedStyle(e).backgroundColor})")
    assert int(style['weight'])>=700 and style['bg']!='rgba(0, 0, 0, 0)'
    page.screenshot(path=str(_screenshots_dir / 'founder-dialog-vertical-four-options.png'),full_page=True)

def test_founder_decision_outside_click_dismisses_and_persistent_bar_reopens(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[]
    live['control_snapshot']={'mode':'working'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    decision={'type':'founder_decision','title':'Choose one','prompt':'Choose to continue','options':[{'value':'1','label':'A','summary':'First'},{'value':'2','label':'B','summary':'Second'}]}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"conops",payload:{waiting_input:true,decision},ts:1720000300})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();button=dialog.locator('.decision-submit')
    assert float(button.evaluate("e=>getComputedStyle(e).borderRadius.replace('px','')"))>=12
    page.locator('#modal').click(position={'x':5,'y':5});assert dialog.count()==0
    launch=page.locator('#opendecision');assert launch.is_visible()
    assert page.locator('.live-indicator').get_by_text('Waiting for your decision').is_visible()
    launch.click();assert page.locator('.decision-dialog').is_visible()

def test_decision_ready_bar_is_first_and_sticky_during_page_scroll(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','title':'Choose the operating concept','prompt':'Choose','options':[{'value':'1','label':'A'},{'value':'2','label':'B'}]}
    live['events']=[{'id':1,'kind':'founder_decision','message':'Choose','phase_key':'conops','payload':{'waiting_input':True,'question':'Choose','decision':decision},'ts':1720000300}]
    live['ui_state']={'mode':'engine_decision','active_phase':live['active_phase'],'process_live':True,'decision':decision,'gate':None}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.set_viewport_size({'width':1390,'height':620});page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape')
    launch=page.locator('#opendecision');panel=page.locator('.detail-layout>div>.panel');assert launch.is_visible()
    assert panel.evaluate("e=>e.firstElementChild.nextElementSibling?.id==='opendecision'")
    assert launch.evaluate("e=>getComputedStyle(e).position")=='sticky'
    page.evaluate('window.scrollTo(0,document.body.scrollHeight)');page.wait_for_timeout(100)
    box=launch.bounding_box();assert box and box['y']>=80 and box['y']<160
    assert launch.get_by_text('Required to continue',exact=False).is_visible()
    launch.click();assert page.locator('.decision-dialog').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'decision-ready-sticky.png'),full_page=False)

def test_answered_decision_clears_and_binary_approval_uses_same_dialog(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();concept={'type':'founder_decision','title':'Old concept decision','prompt':'Choose','options':[{'value':'1','label':'A'},{'value':'2','label':'B'}]}
    live['events']=[{'id':10,'kind':'founder_decision','message':'Choose','phase_key':'conops','payload':{'waiting_input':True,'decision':concept},'ts':1720000000},{'id':11,'kind':'answer','message':'You: 1 - reason','phase_key':'conops','payload':{'transcript':True},'ts':1720000001}]
    submitted=[];page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.route('**/api/tasks/live/answer',lambda route:(submitted.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for();assert page.locator('#opendecision').count()==0 and page.locator('.decision-dialog').count()==0
    approval={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'answer_format':'<value>','options':[{'value':'y','label':'Approve','summary':'Continue'},{'value':'n','label':'Reject','summary':'Stop'}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':approval};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':approval,'gate':None}
    page.unroute('**/api/tasks/live');page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"intent",payload:{waiting_input:true,decision},ts:1720000300})',approval)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.get_by_text('Approve',exact=True).is_visible() and dialog.get_by_text('Reject',exact=True).is_visible();assert dialog.locator('.decision-why').is_hidden()
    page.screenshot(path=str(_screenshots_dir / 'typed-intent-approval.png'),full_page=True)
    assert dialog.locator('.decision-submit').is_hidden();dialog.get_by_text('Approve',exact=True).click();page.locator('.decision-dialog').wait_for(state='detached');assert submitted==[{'answer':'y','decision_id':'intent-approval','expected_sequence':None}]
    assert page.locator('#opendecision').count()==0 and page.locator('.live-indicator').get_by_text('Decision received',exact=False).is_visible()


def test_stale_structured_decision_is_not_actionable_on_load(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'old-intent','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[{'id':91,'kind':'founder_decision','message':'Approve?','phase_key':'intent','payload':{'waiting_input':True,'decision':decision},'ts':1720000300}]
    live['input_state']={'waiting':False,'process_live':False,'decision':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    assert page.locator('#opendecision').count()==0 and page.locator('.decision-dialog').count()==0


def test_stale_decision_conflict_closes_and_refreshes_dialog(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[];live['input_state']={'waiting':True,'process_live':True,'decision':decision};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':decision,'gate':None};calls={'get':0}
    def task(route):
        calls['get']+=1
        if calls['get']>1:live['input_state']={'waiting':False,'process_live':False,'decision':None};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'needs','process_live':True,'decision':None,'gate':None}
        route.fulfill(json=_promote_control(live))
    page.route('**/api/tasks/live',task)
    page.route('**/api/tasks/live/answer',lambda route:route.fulfill(status=409,json={'detail':{'error':'Stale founder decision was not submitted.','cause':'The submitted decision is no longer open.','next':'Refresh shows the current state.','stale':True,'current_state':'running','active_phase':'needs'}}))
    page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for();dialog.get_by_text('Reject',exact=True).click()
    page.get_by_text('Stale founder decision was not submitted.',exact=False).wait_for();page.wait_for_timeout(900)
    assert page.locator('.decision-dialog').count()==0 and page.locator('#opendecision').count()==0 and calls['get']>=2

def test_intent_approval_shows_selected_concept_and_founder_reason(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'context':{'label':'Selected concept','value':'PWA','reason':'You stated you chose this concept because one deployment fits our team'},'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[{'id':91,'kind':'founder_decision','message':'Approve?','phase_key':'intent','payload':{'waiting_input':True,'decision':decision},'ts':1720000300}];live['input_state']={'waiting':True,'process_live':True,'decision':decision}
    live['ui_state']={'mode':'engine_decision','active_phase':live['active_phase'],'process_live':True,'decision':decision,'gate':None}
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view')
    context=page.locator('.decision-context');context.wait_for();assert context.get_by_text('PWA',exact=True).is_visible()
    assert context.get_by_text('You stated you chose this concept because one deployment fits our team',exact=True).is_visible()
    page.screenshot(path=str(_screenshots_dir / 'intent-approval-with-concept-reason.png'),full_page=True)

def test_browser_logs_runtime_version_and_reloads_when_server_changes(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    console=[];page.on('console',lambda msg:console.append(msg.text))
    page.goto(ui+'/');page.wait_for_timeout(300)
    assert any('runtime version' in line and 'development' in line for line in console)
    # The watcher contract compares the server runtime and reloads on mismatch.
    source=(Path(__file__).parents[1]/'src/delivery_api/web/identity.js').read_text()
    assert "live.runtime_id!==runtimeVersion" in source and 'location.reload()' in source

def test_detail_uses_one_large_v_process_without_duplicate_path_map(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    page.goto(ui+'/tasks/live/view');phases=page.locator('.phase-list-primary .phase');phases.first.wait_for()
    assert phases.count()==8 and page.locator('.detail-layout .v-map').count()==0 and page.locator('.detail-layout .gate-legend').count()==0
    first=phases.first.bounding_box();assert first['height']>=70

def test_server_accepted_decision_event_closes_founder_box(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':decision};live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':decision,'gate':None,'show_start':False,'show_progress':True,'show_engine_decision':True,'show_question':False,'show_answer_composer':False,'show_phase_gate':False,'show_artifacts':False,'show_console':True};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'};calls={'n':0}
    def snapshot(route):
        calls['n']+=1
        route.fulfill(json=_promote_control(live))
    page.route('**/api/tasks/live',snapshot)
    page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for()
    live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    page.evaluate("liveSource.emit({kind:'answer',message:'You: y',phase_key:'intent',payload:{decision_id:'intent-approval',answer:'y'},ts:1720000400})")
    page.locator('.decision-dialog').wait_for(state='detached');assert page.locator('#opendecision').count()==0 and calls['n']>=2

def test_needs_gate_state_hides_stale_intent_prompt_and_opens_needs_gate(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();gate=next(g for g in live['gates'] if g['after_phase']=='needs');gate['state']='waiting'
    live['control_snapshot']={'mode':'waiting_gate','wait_id':gate['gate_key']};live.update({'state':'waiting_gate','active_phase':'needs','input_state':{'waiting':False,'process_live':False,'decision':None},
                 'ui_state':{'mode':'phase_gate','active_phase':'needs','process_live':False,'decision':None,'gate':gate,'show_start':False,'show_progress':False,'show_engine_decision':False,'show_question':False,'show_answer_composer':False,'show_phase_gate':True,'show_artifacts':False,'show_console':True}})
    live['events']=[{'id':1,'kind':'waiting_input','message':'Approve this intent model? [y/N]','phase_key':'intent','payload':{'waiting_input':True,'question':'Approve this intent model? [y/N]'},'ts':1720000000}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view')
    page.locator('#openphasegate').wait_for();assert page.locator('.current-question').is_hidden() and page.locator('#answerform').is_hidden()
    page.locator('#openphasegate').click();page.locator('#gateform').wait_for()
    assert page.locator('.decision-dialog').count()==0 and page.locator('#opendecision').count()==0
    assert page.get_by_role('heading',name='Stakeholder needs gate').is_visible()


def test_managed_task_state_reducer_is_exhaustive_and_atomic(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){this.closed=true}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json()
    decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    calls={'n':0}
    def route(request):
        calls['n']+=1
        if calls['n']>1:
            live['input_state']={'waiting':True,'process_live':True,'decision':decision};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'}
            live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':decision,'gate':None}
        request.fulfill(json=_promote_control(live))
    page.route('**/api/tasks/live',route);page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"intent",payload:{waiting_input:true,decision},ts:1720000500})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.get_by_text('Approve',exact=True).is_visible()
    assert page.locator('#answerform').is_hidden() and page.locator('#opendecision').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'managed-state-intent-decision.png'),full_page=True)
    app_source=(Path(__file__).parents[1]/'src/delivery_api/web/task.js').read_text();state_source=(Path(__file__).parents[1]/'src/delivery_api/web/ui-state.js').read_text()
    assert 'reduceTaskSnapshot' in app_source and 'next.mode!==view.mode' in app_source and 'TASK_UI_ACTIONS' in state_source


def test_runtime_build_identity_is_visible(page,ui):
    page.goto(ui+'/');page.locator('#accountbutton').click();page.locator('#opendebug').click();badge=page.locator('.debug-dialog .runtime-id');badge.wait_for();assert badge.inner_text()=='BUILD develop'


def test_sse_snapshot_refresh_is_filtered_and_coalesced(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){this.closed=true}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();calls={'n':0}
    def route(request):calls.__setitem__('n',calls['n']+1);request.fulfill(json=_promote_control(live))
    page.route('**/api/tasks/live',route);page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for();baseline=calls['n']
    page.evaluate("""for(let i=0;i<100;i++)liveSource.emit({kind:'progress',message:'line '+i,phase_key:'intent',payload:{},ts:1720001000+i})""")
    page.wait_for_timeout(200);assert calls['n']==baseline
    page.evaluate("""for(let i=0;i<50;i++)liveSource.emit({kind:'founder_decision',message:'decision '+i,phase_key:'intent',payload:{waiting_input:true},ts:1720002000+i})""")
    page.wait_for_timeout(250);assert calls['n']==baseline+1
    source=(Path(__file__).parents[1]/'src/delivery_api/web/task.js').read_text()
    assert "stateEvents=new Set" in source and 'snapshotPending' in source and 'setTimeout(snapshotRefresh,75)' in source


def test_runtime_badge_survives_identity_render(page,ui):
    page.route('**/health',lambda route:route.fulfill(json={'ok':True,'runtime_id':'7d6647f123456','repositories':{}}))
    page.route('**/api/auth',lambda route:(page.wait_for_timeout(150),route.fulfill(json={'mode':'basic','authenticated':True,'actor':'shy@example.com','admin':True,'providers':[]})))
    page.goto(ui+'/');page.locator('#accountbutton').click();page.locator('#opendebug').click();badge=page.locator('.debug-dialog .runtime-id');badge.wait_for();assert badge.inner_text()=='BUILD 7d6647f'
    assert page.locator('#accountbutton').get_attribute('aria-label')=='Account menu for shy@example.com';assert page.locator('.debug-dialog .runtime-id').inner_text()=='BUILD 7d6647f'


def test_orphaned_running_task_renders_visible_recovery_error(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='intent';live['input_state']={'waiting':False,'process_live':False,'decision':None};live['ui_state']={'mode':'error','active_phase':'intent','process_live':False,'decision':None,'gate':None};live['events'].append({'id':999,'kind':'error','message':'Error: engine stream handling stopped. Likely cause: protocol failure. Next: restart Requi.re.','phase_key':'intent','payload':{},'ts':1720003000})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view')
    notice=page.locator('.task-error-state');notice.wait_for();assert notice.get_by_text('Task needs attention',exact=True).is_visible()
    assert page.locator('.live-indicator').get_by_text('Task needs attention',exact=True).is_visible()

def test_gate_checkbox_updates_in_place_without_navigation(page,ui):
    live=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in live['gates'] if g['state']=='waiting');live['ui_state']={'mode':'phase_gate','active_phase':live['active_phase'],'process_live':False,'decision':None,'gate':gate};calls=[]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/sim/gates/*/toggle',lambda route:(calls.append(route.request.post_data_json),route.fulfill(json={**gate,'is_open':route.request.post_data_json['is_open']})))
    navigations=[];page.on('framenavigated',lambda frame:navigations.append(frame.url) if frame==page.main_frame else None)
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-intent');page.locator('#gateform').wait_for();page.locator('.model-panel summary').click();before=len(navigations);box=page.locator('#gateopen');box.check()
    page.get_by_text('Gate opened.',exact=True).wait_for();assert box.is_checked() and calls==[{'is_open':True}]
    assert len(navigations)==before and page.locator('#gateform').is_visible()

def test_gate_page_shows_requirements_evidence_and_full_content(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['after_phase']=='srs');gate.update({'state':'waiting','is_open':0})
    task['artifacts']=[{'phase_key':'srs','name':'srs.json','title':'Requirements','stale':False,'present':True,'generation':1}]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=task))
    page.route('**/api/tasks/sim/artifacts/srs/srs.json/view',lambda route:route.fulfill(json={'name':'srs.json','title':'Requirements','phase':'srs','format':'markdown','content':'# Software Requirements Specification - 2b045c5f-9df1-4bc0-afb9-32ac915185b4\n\n## Visible status\n\nThe system shall show status.'}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-srs')
    evidence=page.locator('.gate-evidence');evidence.locator('.artifact-readable').wait_for()
    assert evidence.locator('.gate-artifact-summary').count()==0  # no raw duplicate above the formatted document
    assert evidence.locator('.artifact-readable h2').inner_text()=='Visible status'
    head=evidence.locator('.gate-artifact summary')  # the document title sits in the chevron row; no empty row, no repeated H1
    assert head.inner_text().strip()=='Software Requirements Specification'
    assert head.get_attribute('title')=='2b045c5f-9df1-4bc0-afb9-32ac915185b4'  # the UUID is only a tooltip
    assert '2b045c5f' not in evidence.inner_text()
    assert evidence.locator('.artifact-readable h1').count()==0
    assert page.locator('#gateform').locator('[data-action="approve"]').is_enabled()
    page.screenshot(path=str(_screenshots_dir / 'full-gate-requirements-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert evidence.locator('.artifact-readable').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'full-gate-requirements-phone.png'),full_page=True)


def test_gate_transition_reconciles_delayed_next_phase(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/sim').json();gate=live['gates'][0]
    gate.update({'state':'waiting','is_open':0})
    live.update({'state':'waiting_gate','active_phase':gate['after_phase'],
                 'control_snapshot':{'version':2,'mode':'waiting_gate','owner':'delivery','wait_id':gate['gate_key'],'gate':gate,'phase_key':gate['after_phase']}})
    live['artifacts']=[{'phase_key':gate['after_phase'],'name':'intent_model.json','title':'Needs and Scope','stale':False,'present':True,'valid':True,'generation':1}]
    progressed={**live,'state':'running','active_phase':'conops','control_snapshot':{'version':2,'mode':'working','owner':'delivery','phase_key':'conops'},
                'phases':[{**p,'state':'running'} if p['phase_key']=='conops' else p for p in live['phases']]}
    reads={'count':0}
    def task(route):
        reads['count']+=1
        route.fulfill(json=_promote_control(progressed if reads['count']>3 else live))
    page.route('**/api/tasks/sim',task)
    page.route('**/api/tasks/sim/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'title':'Needs and Scope','format':'json','content':'{}'}))
    page.route('**/api/tasks/sim/gates/*/decide',lambda route:route.fulfill(json={**gate,'state':'decided'}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-intent')
    page.locator('[data-action="approve"]').click()
    notice=page.locator('#phase-transition-status')
    notice.wait_for()
    assert 'Starting Concept of operations' in notice.inner_text()
    page.screenshot(path=str(_screenshots_dir / 'jev-d7-phase-transition-desktop.png'),full_page=True)
    # State events refresh immediately; the timer is only the 60-second safety net.
    for _ in range(2):
        before=reads['count']
        page.evaluate("window.liveSource.handlers.progress({data:JSON.stringify({id:2147483647,kind:'command',phase_key:'conops',message:'Next phase started'})})")
        for tick in range(50):
            if reads['count']>before:break
            page.wait_for_timeout(50)
        assert reads['count']>before
        page.wait_for_timeout(100)
    expect(notice).to_have_count(0,timeout=12000)
    assert reads['count']>3

def test_architecture_gate_identifies_model_proposed_decision(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['after_phase']=='architecture');gate['state']='waiting'
    task['artifacts']=[{'phase_key':'architecture','name':'architecture.json','title':'Architecture','stale':False,'present':True,'valid':True,'generation':1}]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=_promote_control(task)))
    page.route('**/api/tasks/sim/artifacts/architecture/architecture.json/view',lambda route:route.fulfill(json={
        'name':'architecture.json','title':'Architecture','phase':'architecture','format':'json','content':'{"decisions":[]}',
        'decision_provenance':[{'decision_id':'ADR-4','title':'SQLite storage','label':'Model-proposed decision',
                                'founder_evidence':[],'baseline_refs':[]}]}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-architecture')
    panel=page.locator('.decision-provenance');panel.wait_for()
    assert panel.get_by_text('ADR-4').is_visible()
    assert panel.get_by_text('Model-proposed decision').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'jev-d7-architecture-provenance-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(_screenshots_dir / 'jev-d7-architecture-provenance-phone.png'),full_page=True)

def test_gate_page_missing_artifact_blocks_approval(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['after_phase']=='srs');gate['state']='waiting'
    task['artifacts']=[]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=task))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-srs')
    page.locator('#gateform').wait_for()
    assert page.locator('#gate-artifacts').get_by_text('No current valid artifact is available',exact=False).is_visible()
    assert not page.locator('#gateform').locator('[data-action="approve"]').is_enabled()
    # a disabled primary always says why, next to the buttons
    expect(page.locator('#decision-checklist')).to_contain_text('no current artifact to approve')
    assert page.locator('[data-action="approve"]').inner_text().startswith('Approve ')
    page.screenshot(path=str(_screenshots_dir / 'gate-blocked-checklist.png'),full_page=True)


def test_gate_page_refreshes_arriving_artifact_without_losing_form_input(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.gateSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){this.closed=true}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['after_phase']=='srs');gate['state']='waiting'
    task['artifacts']=[]
    task['control_snapshot']={'mode':'waiting_gate','wait_id':'gate-srs'}
    changed={'ready':False}
    def snapshot(route):
        data={**task,'artifacts':([{'phase_key':'srs','name':'srs.json','title':'Requirements','stale':False,'present':True,'generation':1}] if changed['ready'] else [])}
        route.fulfill(json=data)
    page.route('**/api/tasks/sim',snapshot)
    page.route('**/api/tasks/sim/artifacts/srs/srs.json/view',lambda route:route.fulfill(json={'name':'srs.json','title':'Requirements','phase':'srs','format':'markdown','content':'# Requirements\n\nThe system works.'}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-srs');page.locator('#gate-artifacts').get_by_text('No current valid artifact',exact=False).wait_for()
    page.locator('[name="edited_text"]').fill('Keep this review note')
    assert not page.locator('[data-action="approve"]').is_enabled()
    changed['ready']=True
    page.evaluate("gateSource.emit({kind:'artifact',phase_key:'srs',message:'artifact ready',payload:{}})")
    expect(page.locator('#gate-decision-status')).to_contain_text('Gate evidence changed')
    assert not page.locator('[data-action="approve"]').is_enabled()
    assert page.locator('[name="edited_text"]').input_value()=='Keep this review note'
    page.screenshot(path=str(_screenshots_dir / 'jev-d6-live-gate-evidence.png'),full_page=True)


def test_gate_page_explains_controls_and_tooltips_desktop_and_phone(page,ui):
    live=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in live['gates'] if g['state']=='waiting');live['ui_state']={'mode':'phase_gate','active_phase':live['active_phase'],'process_live':False,'decision':None,'gate':gate}
    mock_gate_review(page,ui,'sim')
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/sim/gates/gate-intent');dialog=page.locator('#gateform');dialog.wait_for()
    for text in ['Approve moves on','Save edit records your wording','Ask for changes regenerates this phase']:
        assert dialog.get_by_text(text,exact=False).is_visible()
    page.locator('.model-panel summary').click()
    help_button=dialog.get_by_role('button',name='What does opening a gate do?');help_button.focus();tip=dialog.get_by_role('tooltip').filter(has_text='auto-approves when the phase arrives');expect(tip).to_be_visible()
    page.screenshot(path=str(_screenshots_dir / 'gate-help-desktop.png'),full_page=True)
    mock_gate_review(page,ui,'sim')
    page.set_viewport_size({'width':390,'height':844});page.goto(ui+'/tasks/sim/gates/gate-intent');dialog=page.locator('#gateform');dialog.wait_for();page.locator('.model-panel summary').click();dialog.get_by_role('button',name='What does opening a gate do?').focus()
    tip=dialog.get_by_role('tooltip').filter(has_text='auto-approves when the phase arrives');expect(tip).to_be_visible();box=tip.bounding_box();assert box['x']>=0 and box['x']+box['width']<=390
    page.screenshot(path=str(_screenshots_dir / 'gate-help-phone.png'),full_page=True)


def test_decision_dialog_rationale_help_is_real_and_keyboard_accessible(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'concept','title':'Choose one','prompt':'Which concept?','rationale_required':True,'options':[{'value':'1','label':'Native'},{'value':'2','label':'PWA'}]};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for();button=dialog.get_by_role('button',name='Why a rationale is requested');button.focus();expect(dialog.get_by_role('tooltip')).to_be_visible()
    expect(dialog.get_by_role('tooltip').get_by_text('stored with the selected option',exact=False)).to_be_visible()

def test_debug_dialog_contains_run_events_desktop_and_phone(page,ui):
    page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    assert page.locator('#console').count()==0
    page.locator('#accountbutton').click();page.locator('#opendebug').click()
    pane=page.locator('#debugexecution details',has=page.locator('summary',has_text='Run events'))
    pane.locator('summary').click();pane.locator('pre').get_by_text('Generating CONOPS option 2/3',exact=False).wait_for()
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('.debug-dialog').is_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


def test_mutation_flows_refresh_in_place_without_document_navigation(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}close(){}}window.EventSource=FakeEventSource""")
    base=page.request.get(ui+'/api/tasks/live').json();base.update({'task_id':'mutation-test','title':'Mutation test','state':'not_started','active_phase':None,'ui_state':{'mode':'not_started','active_phase':None,'process_live':False,'decision':None,'gate':None}});renamed={**base,'title':'Renamed in place'};reads=0
    def mutation_task(route):
        nonlocal reads
        if route.request.method=='PATCH':route.fulfill(json={'status':'renamed'})
        else:reads+=1;route.fulfill(json=_promote_control(base if reads==1 else renamed))
    page.route('**/api/tasks/mutation-test',mutation_task);page.goto(ui+'/tasks/mutation-test/view');page.get_by_role('button',name='Rename').click();page.locator('#renameform input').fill('Renamed in place')
    nav=[];page.on('framenavigated',lambda frame:nav.append(frame.url) if frame==page.main_frame else None);before=len(nav)
    page.locator('#renameform').get_by_role('button',name='Save name').click();page.get_by_role('heading',name='Renamed in place').wait_for();assert len(nav)==before

    live=page.request.get(ui+'/api/tasks/sim').json();gate=live['gates'][0];gate.update({'state':'waiting','is_open':0});live.update({'state':'waiting_gate','active_phase':gate['after_phase'],'ui_state':{'mode':'phase_gate','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':gate},'control_snapshot':{'mode':'waiting_gate','owner':'delivery','wait_id':gate['gate_key']}})
    decided={**live,'state':'running','ui_state':{'mode':'error','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':None}};calls=0
    def task(route):
        nonlocal calls;calls+=1;route.fulfill(json=_promote_control(live if calls==1 else decided))
    page.route('**/api/tasks/sim',task);page.route('**/api/tasks/sim/gates/*/decide',lambda route:route.fulfill(json={**gate,'state':'decided'}))
    page.goto(ui+'/tasks/sim/view');page.locator('#openphasegate').click();page.locator('#gateform').wait_for()
    assert '/tasks/sim/gates/gate-intent' in page.url
    assert not page.locator('[data-action="approve"]').is_enabled()
    assert page.get_by_text('No current valid artifact is available',exact=False).is_visible()

def test_live_progress_scope_and_account_menu_desktop_and_phone(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();phase=next(p for p in live['phases'] if p['phase_key']==live['active_phase']);phase.update({'completed':8,'total':20,'started_at':time.time()-12*60})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/health',lambda route:route.fulfill(json={'ok':True,'runtime_id':'abcdef123456','repositories':{}}))
    page.route('**/api/auth',lambda route:route.fulfill(json={'mode':'basic','authenticated':True,'actor':'shy@example.com','admin':True,'providers':[]}))
    page.set_viewport_size({'width':1365,'height':800});page.goto(ui+'/tasks/live/view')
    scope=page.locator('.progress-scope');scope.wait_for();assert scope.locator('b').all_inner_texts()==['8','20','12'];assert 'out of' in scope.inner_text() and 'minutes so far' in scope.inner_text()
    page.locator('#accountbutton').click();menu=page.locator('#accountmenu');menu.wait_for();assert menu.get_by_text('shy@example.com',exact=True).is_visible();assert menu.get_by_role('menuitem',name='Debug',exact=True).is_visible();assert menu.get_by_role('menuitem',name='Sign out').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'live-scope-account-desktop.png'),full_page=True);page.keyboard.press('Escape');assert menu.is_hidden()
    page.set_viewport_size({'width':390,'height':844});page.reload();scope.wait_for();page.locator('#accountbutton').click();menu.wait_for();page.screenshot(path=str(_screenshots_dir / 'live-scope-account-phone.png'),full_page=True)

def test_recovered_run_hides_stale_sigterm_error_from_live_console(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='intent';live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    live['events']=[
      {'id':1,'kind':'error','message':'Error: interview engine stopped with exit code -15.','phase_key':'intent','payload':{'exit_code':-15},'ts':1720000000},
      {'id':2,'kind':'command','message':'Engine process started','phase_key':'intent','payload':{},'ts':1720000100},
      {'id':3,'kind':'progress','message':'Recovered checkpoint is running','phase_key':'intent','payload':{},'ts':1720000101},
    ]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.live-indicator').wait_for()
    assert page.locator('#console').count()==0;assert page.locator('.task-error-state').count()==0

def test_live_elapsed_uses_latest_phase_command_not_parked_phase_start(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();phase=next(p for p in live['phases'] if p['phase_key']==live['active_phase']);phase.update({'started_at':time.time()-22*3600,'completed':None,'total':None})
    live['events'].append({'id':999,'kind':'command','message':'Engine process started','phase_key':live['active_phase'],'payload':{},'ts':time.time()-12*60})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');scope=page.locator('.progress-scope');scope.wait_for()
    assert '12 minutes so far' in scope.inner_text();assert '22 hours' not in scope.inner_text()

@pytest.mark.ui_cosmetic
def test_avatar_menu_does_not_emit_native_runtime_tooltip(page,ui):
    page.goto(ui+'/');button=page.locator('#accountbutton');button.wait_for();assert page.locator('#identity').get_attribute('title') is None

def test_orchestrator_baseline_decision_renders_without_engine_process(page,ui):
    decision={'type':'founder_decision','decision_id':'system-baseline','title':'Choose the architecture baseline','prompt':'Greenfield or existing?','rationale_required':False,'options':[{'value':'greenfield','label':'Greenfield'},{'value':'existing','label':'Existing system'}]}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['input_state']={'waiting':True,'process_live':True,'decision':decision};live['control_snapshot']={'mode':'waiting_input','owner':'orchestrator','kind':'choice'};live['ui_state']={'mode':'engine_decision','active_phase':'architecture','process_live':True,'decision':decision,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for()
    assert dialog.get_by_text('Greenfield',exact=True).is_visible();assert page.locator('.task-error-state').count()==0

def test_account_menu_is_plain_list_with_explicit_actions(page,ui):
    page.goto(ui+'/');page.locator('#accountbutton').click();menu=page.locator('#accountmenu');menu.wait_for()
    assert menu.locator(':scope > li').count()==3;assert menu.locator('button').all_inner_texts()==['Debug','Sign out']

def test_existing_system_requirement_names_file_format_path_and_next_step(page,ui):
    requirement={'kind':'external_input','title':'Existing-system baseline required','summary':'Architecture is paused until real system facts are supplied.','path':'/artifacts/session/spec/system-baseline.json','format':'SystemBaseline JSON','required_fields':['baseline_id','system_name','mode: existing','repositories (at least one)'],'next':'Save the file, then restart Requi.re; recovery will launch Architecture.'}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['active_work']={'owner':'requirement','phase':'architecture','status':'waiting','started_at':time.time(),'durable_ref':requirement['path'],'requirement':requirement};live['input_state']={'waiting':True,'process_live':True,'decision':None,'requirement':requirement};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');card=page.locator('.requirement-dialog');card.wait_for()
    assert card.get_by_text('SystemBaseline JSON',exact=True).is_visible();assert card.get_by_text(requirement['path'],exact=True).is_visible();assert card.get_by_text('repositories (at least one)',exact=True).is_visible();assert page.locator('.task-error-state').count()==0

def test_missing_resource_dialog_has_recovery_paths_and_greenfield_confirmation(page,ui):
    requirement={'kind':'external_input','title':'Existing-system baseline required','summary':'Architecture needs real system facts.','path':'/artifacts/spec/system-baseline.json','format':'SystemBaseline JSON','required_fields':['baseline_id','system_name','mode: existing','repositories (at least one)'],'next':'Save then continue.'}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['active_work']={'owner':'requirement','phase':'architecture','status':'waiting','started_at':time.time(),'durable_ref':requirement['path'],'requirement':requirement};live['input_state']={'waiting':True,'process_live':True,'decision':None,'requirement':requirement};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');dialog=page.locator('.requirement-dialog');dialog.wait_for()
    assert dialog.get_by_text(requirement['path'],exact=True).is_visible();assert dialog.get_by_role('button',name="I've added the file · check it").is_visible();assert dialog.get_by_role('button',name='Copy fact checklist').is_visible();dialog.get_by_role('button',name='I chose Existing by mistake').click();confirm=page.get_by_role('dialog',name='Confirm Greenfield correction');confirm.wait_for();assert confirm.get_by_role('button',name='Switch & continue').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'missing-resource-recovery.png'),full_page=True)

def test_everything_zip_includes_runtime_snapshot_and_resource_decision(page,ui,tmp_path):
    import zipfile
    requirement={'kind':'external_input','title':'Baseline required','path':'/tmp/system-baseline.json','format':'SystemBaseline JSON'}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['active_work']={'owner':'requirement','phase':'architecture','status':'waiting','started_at':time.time(),'durable_ref':requirement['path'],'requirement':requirement};live['input_state']={'waiting':True,'process_live':True,'decision':None,'requirement':requirement,'question':None,'input_id':None};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.requirement-dialog').wait_for();page.keyboard.press('Escape');page.locator('#accountbutton').click();page.get_by_role('menuitem',name='Debug',exact=True).click()
    with page.expect_download() as info:
        page.get_by_role('button',name='Download everything ZIP',exact=True).click()
    target=tmp_path/'everything.zip';info.value.save_as(str(target))
    with zipfile.ZipFile(target) as z:
        assert z.namelist()==["requi-everything.txt"];text=z.read("requi-everything.txt").decode("utf-8")
    debug_section=json.loads(text.split('===== Debug info =====\n',1)[1].split('\n\n=====',1)[0])
    assert debug_section['task_id']=='live';assert '"owner": "requirement"' in text and '"detected": true' in text and '"task_id": "live"' in text
    assert text.startswith('===== Known omissions =====')

def test_copy_diagnostic_summary_copies_a_bounded_digest(page,ui):
    page.add_init_script("""window.__copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.__copied=text}})""")
    page.goto(ui+'/tasks/live/view');page.locator('.detail-head').wait_for()
    page.locator('#accountbutton').click();page.get_by_role('menuitem',name='Debug',exact=True).click()
    page.get_by_role('button',name='Copy diagnostic summary',exact=True).click();page.wait_for_function("window.__copied.length>0",timeout=30000)
    copied=page.evaluate('window.__copied')
    assert copied.startswith('Requi.re diagnostic summary') and '== Current state ==' in copied and 'Event coverage:' in copied
    page.screenshot(path=str(_screenshots_dir / 'debug-dialog.png'),full_page=True)

def test_greenfield_confirmation_is_single_flight(page,ui):
    requirement={'kind':'external_input','title':'Existing-system baseline required','summary':'Architecture needs facts.','path':'/artifacts/session/spec/system-baseline.json','format':'SystemBaseline JSON','required_fields':[]}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['active_work']={'owner':'requirement','phase':'architecture','status':'waiting','started_at':time.time(),'durable_ref':requirement['path'],'requirement':requirement};live['input_state']={'waiting':True,'process_live':True,'decision':None,'requirement':requirement};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    calls=[]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    def resolve(route):
        calls.append(route.request.post_data);time.sleep(.15);route.fulfill(json={'status':'resolved','action':'greenfield'})
    page.route('**/api/tasks/live/requirements/architecture/resolve',resolve)
    page.goto(ui+'/tasks/live/view');page.locator('.requirement-dialog').wait_for();page.get_by_role('button',name='I chose Existing by mistake').click();confirm=page.get_by_role('button',name='Switch & continue');confirm.dblclick()
    page.wait_for_timeout(250)
    assert len(calls)==1

@pytest.mark.ui_cosmetic
def test_progress_slot_keys_render_as_title_case(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='srs';live['events'].extend([{'id':980,'kind':'progress','message':'stack:pwa_offline_runtime · 12 tok','phase_key':'srs','payload':{},'ts':1720004000},{'id':981,'kind':'error','message':'Schema check found a missing acceptance criterion','phase_key':'srs','payload':{},'ts':1720004001}]);live['artifacts'].append({'phase_key':'srs','name':'srs.json','stale':0,'generation':1});live['ui_state']={'mode':'executing','active_phase':'srs','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('#stageprogress > details > summary').click();feed=page.locator('#stageprogress .progress-item');feed.wait_for()
    assert feed.locator('span').all_inner_texts()==['stack:PWA Offline Runtime · 12 tok']
    assert feed.locator('span').get_attribute('title')=='stack:PWA Offline Runtime · 12 tok'
    got=page.evaluate("""async()=>{const m=await import('/assets/format.js');return ['ui_framework','build_tooling','client_persistence','audio_engine','stack:ui_framework','plan: stack slots','R-12 statement','SPEC-3'].map(m.humanizeSnakeCase)}""")
    assert got==['UI Framework','Build Tooling','Client Persistence','Audio Engine','stack:UI Framework','plan: stack slots','R-12 statement','SPEC-3']
    html=page.evaluate("""async()=>{const m=await import('/assets/phase-output.js');return m.phaseOutputHtml({phase_key:'architecture',status:'ready',items:[{name:'pwa_offline_runtime',statement:'keep_this_detail_as_is'},{name:'Existing Title'}]})}""")
    assert '<b>PWA Offline Runtime</b>' in html and '<b>Existing Title</b>' in html and 'keep_this_detail_as_is' in html

def test_phase_rows_link_to_their_gate_page_once_the_gate_has_opened(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    for n,gate in enumerate(live['gates']):gate['state']='approved' if n==0 else 'idle'
    first=live['gates'][0]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view')
    links=page.locator('.phase-list-primary .phase-gate-link')
    links.first.wait_for()
    assert links.count()==1
    assert links.first.get_attribute('href')=='/tasks/live/gates/'+first['gate_key']
    assert links.first.inner_text()=='Gate'

def test_spec_items_render_as_closed_collapsible_panels_with_formatted_markdown(page,ui):
    page.goto(ui+'/tasks/empty/view')
    body='# SPEC-1 - Course Content Model\n\nKind: data_model. Covers R-1.\n\n## 1. Scope and boundaries\nIn scope:\n- The authored content document shape\n- Identifiers and ordering\n\n## 2. Interface\n1. load()\n2. validate()\n\n| Name | Type |\n|---|---|\n| id | string |\n\nUse `loadCourse` here.'
    html=page.evaluate("""async(body)=>{const m=await import('/assets/gate-presenters.js');const host=document.createElement('div');host.id='spec-host';host.className='gate-structured';host.innerHTML=m.gateStructured({items:[{spec_id:'SPEC-1',kind:'data_model',body_markdown:body},{spec_id:'SPEC-2',title:'Second Spec',body_markdown:'No heading here.'}]});document.body.prepend(host);return host.innerHTML}""",body)
    panels=page.locator('#spec-host details.spec-item')
    assert panels.count()==2
    assert panels.nth(0).locator('summary').inner_text()=='SPEC-1 - Course Content Model'
    assert panels.nth(1).locator('summary').inner_text()=='Second Spec'
    assert panels.nth(0).get_attribute('open') is None  # closed by default
    panels.nth(0).evaluate('d=>{d.open=true}')  # a leftover page modal can intercept real clicks here
    first=panels.nth(0)
    assert first.locator('.spec-body h2').count()==2
    assert first.locator('.spec-body ul li').count()==2 and first.locator('.spec-body ol li').count()==2
    assert first.locator('.spec-body table td').count()==2 and first.locator('.spec-body code').inner_text()=='loadCourse'
    assert '# SPEC-1' not in first.inner_text() and '## 1.' not in first.inner_text()

@pytest.mark.parametrize("viewport", [{"width":1365,"height":850},{"width":390,"height":844}])
def test_progress_is_one_line_without_redundant_phase_and_phases_show_evidence_tooltips(page,ui,viewport):
    page.set_viewport_size(viewport)
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='srs';live['events'].extend([{'id':980,'kind':'progress','message':'A very long progress update that must remain on precisely one line while the full wording stays available on hover','phase_key':'srs','payload':{},'ts':1720004000},{'id':981,'kind':'error','message':'Schema check found a missing acceptance criterion','phase_key':'srs','payload':{},'ts':1720004001}]);live['artifacts'].append({'phase_key':'srs','name':'srs.json','stale':0,'generation':1});live['ui_state']={'mode':'executing','active_phase':'srs','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('#stageprogress > details > summary').click();feed=page.locator('#stageprogress .progress-item');feed.wait_for();span=feed.locator('span');assert span.bounding_box()['width']>0;assert span.bounding_box()['x']+span.bounding_box()['width']<=page.viewport_size['width'];assert feed.locator('small').count()==0;assert span.get_attribute('title').startswith('A very long progress');assert span.evaluate('(e)=>getComputedStyle(e).whiteSpace')=='nowrap';assert span.evaluate('(e)=>getComputedStyle(e).textOverflow')=='ellipsis'
    phase=page.locator('.phase-list-primary .phase').filter(has_text='Requirements');phase.hover();tip=phase.get_by_role('tooltip');tip.wait_for();assert tip.get_by_text('Artifacts: srs.json',exact=True).is_visible();assert tip.get_by_text('missing acceptance criterion',exact=False).is_visible()
    page.screenshot(path=str(_screenshots_dir / f'progress-phase-tooltips-{viewport["width"]}.png'),full_page=True)


def test_dashboard_renders_existing_task_with_final_verification_gate(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    base=page.request.get(ui+f'/api/tasks/{pg_ids["live"]}').json();base['task_id']='daily';base['title']='Daily Habit';base['state']='waiting_gate';base['active_phase']='tests';base['phases'][-1]['state']='awaiting_approval';base['gates']=[g for g in base['gates'] if g.get('after_phase')!='tests'];base['gates'].append({'task_id':'daily','gate_key':'gate-tests','after_phase':'tests','label':'Verification gate','position':6,'is_open':0,'state':'waiting','options':['Approve Verification','Choose another option']})
    page.route('**/api/tasks',lambda route:route.fulfill(json={'tasks':[base]}))
    page.route('**/api/sessions',lambda route:route.fulfill(json={'sessions':[]}))
    page.goto(ui+'/');card=page.locator('.task',has_text='Daily Habit');card.wait_for();assert card.is_visible();assert card.locator('[data-gate="gate-tests"]').count()==1;assert page.get_by_text('Cannot read properties').count()==0

def test_completed_artifacts_have_singleton_tooltips_viewer_and_downloads(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    live=page.request.get(ui+f'/api/tasks/{tid}').json();live['state']='done';live['active_phase']='tests';live['phases'][-1]['state']='approved';live['artifacts']=[{'phase_key':'architecture','name':'architecture.json','ref':'/tmp/architecture.json','mime_type':'application/json','stale':False,'generation':4,'present':True,'valid':True,'validation_error':None}];live['ui_state']={'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}
    page.route(f'**/api/tasks/{tid}',lambda route:route.fulfill(json=_promote_control(live)));page.route(f'**/api/tasks/{tid}/artifacts/architecture/architecture.json/view',lambda route:route.fulfill(json={'name':'architecture.json','phase':'architecture','format':'markdown','display_name':'architecture.md','content':'# Daily Habit Architecture\n\n- Progressive web app','raw_format':'json','raw_content':'{\n  "system": "Daily Habit"\n}'}));page.goto(ui+f'/tasks/{tid}/view');rows=page.locator('.phase-list-primary .phase');rows.nth(4).dispatch_event('mouseenter');rows.nth(4).locator('.phase-tooltip').wait_for(state='visible');assert rows.nth(4).locator('.phase-tooltip').is_visible();rows.nth(4).dispatch_event('mouseleave');rows.nth(5).dispatch_event('mouseenter');assert 'tip-open' not in (rows.nth(4).get_attribute('class') or '');assert 'tip-open' in (rows.nth(5).get_attribute('class') or '');assert page.locator('.download-all').is_visible();page.get_by_role('button',name='architecture.json').click();viewer=page.locator('.artifact-viewer');viewer.wait_for();assert viewer.get_by_role('heading',name='Daily Habit Architecture').is_visible();assert viewer.get_by_text('Progressive web app').is_visible();assert viewer.get_by_text('Show raw json').is_visible();assert viewer.get_by_role('link',name='Download').is_visible();page.screenshot(path=str(_screenshots_dir / 'completed-artifact-viewer.png'),full_page=True)

def test_artifact_viewer_uses_shared_dialog_shell_and_local_close(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    def mutate(live):live.update({'state':'done','artifacts':[{'phase_key':'intent','name':'intent_model.json','title':'Needs and Scope','stale':False,'generation':4,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}],'ui_state':{'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}})
    page.route(f'**/api/tasks/{tid}/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'json','content':'{}'}));route_task_snapshot(page,ui,tid,mutate);page.goto(ui+f'/tasks/{tid}/view');card=page.locator('[data-view-artifact]').first;card.wait_for();assert card.get_by_text('Needs and Scope',exact=True).is_visible();card.click();shell=page.locator('.dialog-shell');viewer=page.get_by_role('dialog',name='Artifact viewer');viewer.wait_for();close=viewer.get_by_role('button',name='Close artifact viewer');assert close.is_visible();vb=viewer.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];assert cb['y']>=vb['y'] and cb['y']+cb['height']<=vb['y']+vb['height'];page.keyboard.press('Escape');assert shell.count()==0
    card.click();page.screenshot(path=str(_screenshots_dir / 'shared-artifact-dialog.png'),full_page=True)

def test_artifact_viewer_shows_json_artifacts_readably(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    def mutate(live):live.update({'state':'done','artifacts':[{'phase_key':'intent','name':'intent_model.json','title':'Needs and Scope','stale':False,'generation':4,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}],'ui_state':{'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}})
    page.route(f'**/api/tasks/{tid}/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'json','content':'{"title":"Ear training","statement":"The app shall play a clip.","items":[{"name":"A clip"}]}'}));route_task_snapshot(page,ui,tid,mutate);page.goto(ui+f'/tasks/{tid}/view');card=page.locator('[data-view-artifact]').first;card.wait_for();assert card.get_by_text('Needs and Scope',exact=True).is_visible();card.click();shell=page.locator('.dialog-shell');viewer=page.get_by_role('dialog',name='Artifact viewer');viewer.wait_for();close=viewer.get_by_role('button',name='Close artifact viewer');assert close.is_visible();vb=viewer.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];assert cb['y']>=vb['y'] and cb['y']+cb['height']<=vb['y']+vb['height'];page.keyboard.press('Escape');assert shell.count()==0
    card.click();dlg=page.locator('.artifact-viewer');dlg.wait_for();assert dlg.locator('pre.artifact-content').count()==0;assert 'The app shall play a clip.' in dlg.inner_text() and '{' not in dlg.locator('.gate-structured').inner_text()

def test_audit_uses_shared_dialog_sortable_filterable_sticky_table(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    live=page.request.get(ui+f'/api/tasks/{tid}').json();page.route(f'**/api/tasks/{tid}',lambda route:route.fulfill(json=_promote_control(live)));entries=[{'ts':1720000200,'actor':'Shy','action':'gate.approve','detail':{'phase':'srs'}},{'ts':1720000100,'actor':'policy','action':'gate.bypassed','detail':{'phase':'needs'}}];page.route(f'**/api/tasks/{tid}/audit',lambda route:route.fulfill(json={'actor_mode':'basic','entries':entries,'totals':{'tokens':1234,'price':2.5,'price_usd':2.5,'phases_priced':2}}));page.goto(ui+f'/tasks/{tid}/view');page.get_by_role('button',name='Audit').click();dialog=page.get_by_role('dialog',name='Task audit');dialog.wait_for();assert dialog.get_by_text('1,234').count()==0 and dialog.get_by_text('2.50 credits').is_visible();headers=dialog.locator('thead');assert headers.evaluate('(e)=>getComputedStyle(e).position')=='sticky';rows=dialog.locator('tbody tr');assert rows.count()==2;dialog.get_by_role('button',name='Actor').click();assert rows.first.locator('td').nth(1).inner_text()=='Shy';dialog.get_by_role('button',name='Actor').click();assert rows.first.locator('td').nth(1).inner_text()=='policy';dialog.get_by_label('Filter audit entries').fill('approve');assert rows.count()==1;assert rows.first.get_by_text('gate.approve').is_visible();close=dialog.get_by_role('button',name='Close task audit');vb=dialog.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];page.screenshot(path=str(_screenshots_dir / 'shared-audit-dialog.png'),full_page=True);page.keyboard.press('Escape');assert dialog.count()==0

def test_model_route_default_and_phase_override_controls(page,ui):
    page.goto(ui+'/tasks/empty/view')
    picker=page.get_by_role('group',name='Default model route');picker.wait_for()
    assert picker.locator('[data-start-route]').count()==2
    assert not page.get_by_role('button',name='Start interview').is_enabled()
    deepseek=picker.locator('[data-start-route=deepseek]')
    assert 'DeepSeek' in deepseek.inner_text()
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-conops')
    page.locator('.model-panel summary').click()
    override=page.locator('select[name="next_model"]');override.wait_for()
    assert override.locator('option').first.inner_text()=='Use current task default'
    assert 'ollama:qwen2.5-coder:14b' in page.locator('[data-model-for=next_model]').inner_text()

def test_artifact_viewer_renders_content_title_bold_and_transcript(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    live=page.request.get(ui+f'/api/tasks/{tid}').json();live['state']='done';live['artifacts']=[{'phase_key':'intent','name':'intent_model.json','stale':False,'generation':1,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}];live['ui_state']={'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}
    payload={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'markdown','display_name':'intent_model.md','content':'# What we understood\n\n**Problem:** Build habits\n\n## Interview questions and answers\n\n### Question 1\nWho needs this?\n\n**Answer:** Busy adults','raw_format':'json','raw_content':'{}'}
    page.route(f'**/api/tasks/{tid}',lambda route:route.fulfill(json=_promote_control(live)));page.route(f'**/api/tasks/{tid}/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json=payload));page.goto(ui+f'/tasks/{tid}/view');page.get_by_role('button',name='intent_model.json').click();viewer=page.locator('.artifact-viewer');viewer.wait_for()
    assert viewer.get_by_role('heading',name='Needs and Scope').is_visible()
    assert viewer.locator('strong').filter(has_text='Problem:').count()==1 and viewer.locator('strong').filter(has_text='Answer:').count()==1
    assert viewer.get_by_text('Who needs this?').is_visible() and viewer.get_by_text('Busy adults').is_visible()
    page.screenshot(path=str(_screenshots_dir / 'intent-viewer-transcript.png'),full_page=True)


def interview_history_snapshot(snapshot):
    # Replace, not extend: the module-scoped server accumulates answer events
    # from earlier tests, so the base snapshot is polluted by suite order.
    snapshot["events"] = [
        {"id": 90001, "kind": "waiting_input", "phase_key": "conops", "ts": 1,
         "message": "What should the family see first?",
         "payload": {"waiting_input": True, "question": "What should the family see first?"}},
        {"id": 90002, "kind": "answer", "phase_key": "conops", "ts": 2,
         "message": "You: A calm shared view", "payload": {"answer": "A calm shared view"}},
        {"id": 90003, "kind": "waiting_input", "phase_key": "conops", "ts": 3,
         "message": "Who else should weigh in?",
         "payload": {"waiting_input": True, "question": "Who else should weigh in?"}},
        {"id": 90004, "kind": "answer", "phase_key": "conops", "ts": 4,
         "message": "You: My co-founder, next week", "payload": {"answer": "My co-founder, next week"}},
    ]


def long_history_snapshot(snapshot):
    words = lambda n, p: " ".join(f"{p}{i}" for i in range(1, n + 1))
    snapshot["events"] = [
        {"id": 91001, "kind": "waiting_input", "phase_key": "conops", "ts": 1,
         "message": words(40, "q"), "payload": {"waiting_input": True, "question": words(40, "q")}},
        {"id": 91002, "kind": "answer", "phase_key": "conops", "ts": 2,
         "message": "You: " + words(33, "a"), "payload": {"answer": words(33, "a")}},
        {"id": 91003, "kind": "waiting_input", "phase_key": "conops", "ts": 3,
         "message": words(32, "r"), "payload": {"waiting_input": True, "question": words(32, "r")}},
        {"id": 91004, "kind": "answer", "phase_key": "conops", "ts": 4,
         "message": "You: " + words(32, "b"), "payload": {"answer": words(32, "b")}},
    ]


def test_interview_history_truncates_questions_and_answers_after_32_words(pg_page, pg_ui, pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    route_task_snapshot(page, ui, tid, long_history_snapshot)
    page.goto(ui + f"/tasks/{tid}/view")
    page.get_by_text("Interview so far").wait_for()
    # newest first: the 40-word/33-word pair is the older one, so it is listed second
    first, second = page.locator(".qa-item").nth(1), page.locator(".qa-item").nth(0)
    q1 = first.locator(".qa-question p").inner_text()
    a1 = first.locator(".qa-answer p").inner_text()
    assert q1 == " ".join(f"q{i}" for i in range(1, 33)) + "\u2026"
    assert a1 == " ".join(f"a{i}" for i in range(1, 33)) + "\u2026"
    # the full text stays reachable on hover
    assert first.locator(".qa-question p").get_attribute("title") == " ".join(f"q{i}" for i in range(1, 41))
    # exactly 32 words is shown whole, no ellipsis
    assert "\u2026" not in second.locator(".qa-question p").inner_text()
    assert "\u2026" not in second.locator(".qa-answer p").inner_text()
    assert len(second.locator(".qa-answer p").inner_text().split()) == 32
    # the comment dialog still shows the full question
    first.locator(".qa-question .qa-comment-btn").click()
    assert "q40" in page.locator(".feedback-context").inner_text()


def test_interview_history_cards_and_debug_toggle_persist(page, ui):
    route_task_snapshot(page, ui, "live", interview_history_snapshot)
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Interview so far").wait_for()
    assert page.locator(".qa-item").count() == 2
    assert page.locator(".qa-item").nth(1).locator(".qa-question p").inner_text() == "What should the family see first?"
    assert page.locator(".qa-item").nth(1).locator(".qa-answer p").inner_text() == "A calm shared view"
    assert page.locator(".qa-item").nth(0).locator(".qa-question p").inner_text() == "Who else should weigh in?"
    assert page.locator(".qa-tag", has_text="Q1").count() == 1
    assert page.locator("#console").count() == 0
    page.locator(".avatar").click();page.locator("#opendebug").click();page.locator('.debug-dialog').wait_for()
    assert page.locator('#console').count()==0
    page.get_by_role('button',name='Close debug').click();page.reload();page.get_by_text('Interview so far').wait_for()
    assert page.locator('#console').count()==0 and page.locator('.qa-item').count()==2


def test_feedback_thumbs_comment_persist_and_delete(pg_page, pg_ui, pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    route_task_snapshot(page, ui, tid, interview_history_snapshot)
    page.goto(ui + f"/tasks/{tid}/view")
    page.get_by_text("Interview so far").wait_for()
    first_q = page.locator(".qa-item").nth(0).locator(".qa-question .qa-actions")
    first_q.locator('[data-thumb="-1"]').click()
    page.locator(".qa-question .qa-thumb.active").first.wait_for()
    assert page.locator(".qa-question .qa-thumb.active").first.get_attribute("data-thumb") == "-1"

    first_q.locator(".qa-comment-btn").click()
    dialog = page.get_by_role("dialog", name="Comment on this question")
    dialog.wait_for()
    assert dialog.get_by_text("ANNOTATION ONLY").is_visible()
    dialog.locator("#feedbacktext").fill("Too abstract for a first question")
    dialog.get_by_role("button", name="Save comment").click()
    note = page.locator(".qa-note", has_text="Too abstract for a first question")
    note.wait_for()
    dialog.wait_for(state="detached")

    page.reload()
    page.get_by_text("Interview so far").wait_for()
    assert page.locator(".qa-question .qa-thumb.active").first.get_attribute("data-thumb") == "-1"
    page.locator(".qa-note", has_text="Too abstract").wait_for()

    page.locator(".qa-note").first.click()
    dialog = page.get_by_role("dialog", name="Comment on this question")
    dialog.wait_for()
    delete = dialog.get_by_role("button", name="Delete feedback")
    delete.click()
    assert dialog.get_by_role("button", name="Confirm delete?").is_visible()
    dialog.get_by_role("button", name="Confirm delete?").click()
    page.get_by_role("dialog", name="Comment on this question").wait_for(state="detached")
    assert page.locator(".qa-note").count() == 0
    assert page.locator(".qa-question .qa-thumb.active").count() == 0


def test_feedback_save_disables_while_pending_and_closes_only_on_success(pg_page, pg_ui, pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    route_task_snapshot(page, ui, tid, interview_history_snapshot)
    page.goto(ui + f"/tasks/{tid}/view")
    page.get_by_text("Interview so far").wait_for()
    page.locator(".qa-answer .qa-comment-btn").first.click()
    dialog = page.get_by_role("dialog", name="Comment on this answer")
    dialog.locator("#feedbacktext").fill("Misread as a refusal")
    save = dialog.get_by_role("button", name="Save comment")
    page.evaluate("""() => {
      window.__feedbackFetch = window.fetch;
      window.__feedbackRequests = 0;
      window.fetch = (url, options) => {
        if (String(url).endsWith('/feedback') && options?.method === 'POST') {
          window.__feedbackRequests++;
          return new Promise(resolve => { window.__failFeedback = () =>
            resolve(new Response(JSON.stringify({detail: 'Try again'}),
              {status: 500, headers: {'Content-Type': 'application/json'}})); });
        }
        return window.__feedbackFetch(url, options);
      };
    }""")
    save.click()
    expect(dialog.locator("#savefeedback")).to_be_disabled()
    assert dialog.is_visible()
    assert page.evaluate("window.__feedbackRequests") == 1
    dialog.locator("#savefeedback").evaluate("button => button.click()")
    assert page.evaluate("window.__feedbackRequests") == 1
    page.evaluate("window.__failFeedback()")
    expect(dialog.locator("#savefeedback")).to_be_enabled()
    assert dialog.is_visible()
    assert dialog.locator("#feedbacktext").input_value() == "Misread as a refusal"
    page.evaluate("window.fetch = window.__feedbackFetch")
    save.click()
    dialog.wait_for(state="detached")
    page.locator(".qa-note", has_text="Misread as a refusal").wait_for()


@pytest.mark.ui_cosmetic
def test_question_comment_columns_follow_character_ratio(pg_page, pg_ui, pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    long_question = "What strategies will you implement to ensure data caching prevents downtime during peak usage?"
    def snapshot(data):
        interview_history_snapshot(data)
        data["events"][0]["message"] = long_question
        data["events"][0]["payload"]["question"] = long_question
    route_task_snapshot(page, ui, tid, snapshot)
    page.goto(ui + f"/tasks/{tid}/view")
    row = page.locator(".qa-question").last   # Q1 (edited above) is the oldest, listed last now that newest is first
    row.wait_for()
    assert row.locator(".qa-notes").count() == 0
    assert row.evaluate("row => getComputedStyle(row).getPropertyValue('--question-parts').trim()") == "12"
    before = row.locator(".qa-main").bounding_box()["width"]
    row.locator(".qa-comment-btn").click()
    dialog = page.get_by_role("dialog", name="Comment on this question")
    comment = "Even a technical founder would struggle with this question"
    dialog.locator("#feedbacktext").fill(comment)
    dialog.get_by_role("button", name="Save comment").click()
    dialog.wait_for(state="detached")
    note = row.locator(".qa-note")
    note.wait_for()
    parts = int(row.evaluate("row => getComputedStyle(row).getPropertyValue('--question-parts')"))
    expected = max(5, min(9, round(12 * len(long_question) / (len(long_question) + len(comment)))))
    assert parts == expected
    assert int(row.evaluate("row => getComputedStyle(row).getPropertyValue('--note-parts')")) == 12 - parts
    question_box = row.locator(".qa-main").bounding_box()
    note_box = row.locator(".qa-notes").bounding_box()
    assert question_box["width"] < before
    assert question_box["width"] > note_box["width"]
    assert question_box["x"] + question_box["width"] <= note_box["x"]
    assert note_box["x"] + note_box["width"] <= row.bounding_box()["x"] + row.bounding_box()["width"]
    assert note.is_visible()
    row.screenshot(path=str(_screenshots_dir / "qa-column-balance-desktop-row.png"))
    page.set_viewport_size({"width": 600, "height": 850})
    assert row.locator(".qa-main").bounding_box()["width"] > 220
    assert row.locator(".qa-notes").bounding_box()["y"] >= row.locator(".qa-main").bounding_box()["y"]
    row.scroll_into_view_if_needed()
    row.screenshot(path=str(_screenshots_dir / "qa-column-balance-mobile-row.png"))
    # A short question still gets five twelfths instead of collapsing to a word-wide strip.
    second = page.locator(".qa-question").first
    second.locator(".qa-comment-btn").click()
    second_dialog = page.get_by_role("dialog", name="Comment on this question")
    second_dialog.locator("#feedbacktext").fill("This is a long note about a brief question, with plenty of additional context to test the lower bound.")
    second_dialog.get_by_role("button", name="Save comment").click()
    second_dialog.wait_for(state="detached")
    assert int(second.evaluate("row => getComputedStyle(row).getPropertyValue('--question-parts')")) == 5
    # Removing the annotation returns the question to the full available width.
    row.locator(".qa-note").click()
    dialog = page.get_by_role("dialog", name="Comment on this question")
    dialog.get_by_role("button", name="Delete feedback").click()
    dialog.get_by_role("button", name="Confirm delete?").click()
    dialog.wait_for(state="detached")
    assert row.locator(".qa-notes").count() == 0
    assert int(row.evaluate("row => getComputedStyle(row).getPropertyValue('--question-parts')")) == 12


def test_debug_chips_show_intent_relevance_and_differed_tooltip(page, ui):
    route_task_snapshot(page, ui, "live", interview_history_snapshot)
    classifications = {"session": "s", "answers": {}}
    def classify(route):
        snapshot = page.request.get(ui + "/api/tasks/live").json()
        interview_history_snapshot(snapshot)
        turns = [(e["payload"]["question"], None) for e in snapshot["events"] if e["kind"] == "waiting_input"]
        answers = {e["payload"]["answer"] for e in snapshot["events"] if e["kind"] == "answer"}
        import hashlib as h
        for (q, _), a in zip(turns, sorted(answers)):
            ref = h.sha256((q + "\n" + a).strip().encode()).hexdigest()[:16]
            classifications["answers"][ref] = {
                "intent": {"jev": {"intent": "answer"}, "baseline": {"intent": "answer"}, "jev_status": "ok", "differed": False, "ts": "t"},
                "coverage": {"jev": {"relevance": "thin"}, "baseline": {"relevance": "relevant"}, "jev_status": "ok", "differed": True, "ts": "t"}}
        route.fulfill(json=classifications)
    page.route("**/api/tasks/live/classifications", classify)
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Interview so far").wait_for()
    assert page.locator('.qa-chips').count()==0
    page.locator('.avatar').click();page.locator('#opendebug').click();page.get_by_text('Answer classifications',exact=True).click()
    page.locator('#debugexecution').get_by_text('"differed": true',exact=False).wait_for()




def test_protocol_422_is_not_mislabeled_engine_down(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    detail="Error: stage 'probing': model ignored the structured interview-turn contract twice."
    live['state']='running';live['active_phase']='intent'
    live['ui_state']={'mode':'error','active_phase':'intent','process_live':False,'diagnostic':{'reason':'engine protocol rejected the snapshot','http_status':422,'code':130,'detail':detail}}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view')
    notice=page.locator('.task-error-state');notice.wait_for()
    assert notice.get_by_text('Engine returned an error',exact=True).is_visible()
    assert 'structured interview-turn contract' in notice.inner_text()
    assert 'Engine is not running' not in notice.inner_text()
    page.screenshot(path=str(_screenshots_dir / 'protocol-error-state.png'),full_page=True)

@pytest.mark.parametrize("failed",[False,True])
def test_legacy_ollama_model_switch_dialog(page,ui,failed):
    live=page.request.get(ui+'/api/tasks/live').json()
    live.update({'state':'failed' if failed else 'running','active_phase':'intent','model_route':'ollama/qwen2.5-coder:14b',
        'simulated':False,'input_state':{'waiting':True,'input_id':'legacy-input','process_live':True},
        'ui_state':{'mode':'error' if failed else 'engine_question','active_phase':'intent','question':'Continue?','process_live':True}})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/live/execution*',lambda route:route.fulfill(json={
        'session_id':'legacy','route':'ollama','usage':[],'calls':[],'usage_segments':[],'raw':[],
        'effective_models':{'probing':['ollama','qwen2.5-coder:14b']}}))
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[
        {'id':'ollama','available':True,'model':'ollama:qwen2.5-coder:14b','models':['qwen2.5-coder:14b','phi4']},
        {'id':'deepseek','available':False,'error':'no key'}]}))
    page.goto(ui+'/tasks/live/view')
    assert page.locator('.execution-data').count()==0
    if failed:expect(page.locator('.task-error-state #switchmodel')).to_be_visible()
    page.locator('#switchmodel').click()
    dialog=page.locator('.route-switch-dialog')
    expect(dialog).to_be_visible()
    expect(page.locator('#route-switch-target')).to_have_value('ollama:phi4')
    expect(page.locator('#confirm-switch')).to_be_enabled()
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)

def test_completed_engine_session_in_error_view_does_not_offer_switch(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live.update({'state':'failed','active_phase':'intent','simulated':False,
        'input_state':{'waiting':False,'input_id':None,'process_live':False,'engine_process_status':40},
        'ui_state':{'mode':'error','active_phase':'intent','process_live':False}})
    live['events'].append({'id':9999,'kind':'error','message':'old template literal failure','phase_key':'intent','payload':{},'ts':1720004001})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/live/execution*',lambda route:route.fulfill(json={
        'session_id':'completed','route':'ollama','usage':[],'calls':[],'usage_segments':[],'raw':[],
        'effective_models':{'probing':['ollama','qwen2.5-coder:14b']}}))
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[]}))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('.task-error-state')).to_be_visible()
    expect(page.locator('.task-error-state')).to_contain_text('Any earlier model error is historical')
    expect(page.locator('.task-error-state')).not_to_contain_text('template literal')
    expect(page.locator('#switchmodel')).to_have_count(0)
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.get_by_text('The linked interview session has no open input and is complete.',exact=False)).to_be_visible()


def test_failed_needs_has_retry_not_generation_rerun(page,ui):
    from playwright.sync_api import expect
    live=page.request.get(ui+'/api/tasks/live').json()
    for phase in live['phases']:
        if phase['phase_key'] in ('intent','conops'):phase['state']='approved'
        elif phase['phase_key']=='needs':phase.update({'state':'failed','detail':'Generator failed before producing needs'})
        else:phase['state']='pending'
    live.update({'state':'failed','active_phase':'needs','input_state':{'waiting':False,'process_live':False,'engine_process_status':40},
        'ui_state':{'mode':'error','active_phase':'needs','process_live':False}})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[]}))
    page.route('**/api/tasks/live/execution*',lambda route:route.fulfill(json={'session_id':'complete-session','route':'ollama','usage':[],'calls':[],'usage_segments':[],'raw':[]}))
    page.route('**/api/tasks/live/retry-needs',lambda route:route.fulfill(json={'status':'preview','phase':'needs','session_id':'complete-session','retains':['intent','conops'],'message':'Retry Stakeholder needs from the approved interview. This may make new model calls and incur cost.'}))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('#retryneeds')).to_be_visible()
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.locator('.phase-list-primary .phase').nth(2)).to_contain_text('Generator failed')
    page.locator('#retryneeds').click()
    dialog=page.get_by_role('dialog',name='Retry Stakeholder needs');expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('may make new model calls and incur cost')
    dialog.get_by_role('button',name='Cancel').click()
    expect(dialog).to_have_count(0)
    page.screenshot(path=str(_screenshots_dir / 'failed-needs-retry.png'),full_page=True)


def test_audit_dialog_scrolls_on_short_viewport(page,ui):
    page.set_viewport_size({'width':900,'height':460})
    live=page.request.get(ui+'/api/tasks/live').json()
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    entries=[{'ts':1720000000+i,'actor':'Founder','action':f'event.{i}','detail':{'index':i,'note':'long audit detail '*8}} for i in range(75)]
    page.route('**/api/tasks/live/audit',lambda route:route.fulfill(json={'actor_mode':'local','entries':entries,'totals':{'tokens':2300,'price':0,'interview_calls':14,'interview_cost_usd':0.0037,'interview_unpriced_calls':0}}))
    page.goto(ui+'/tasks/live/view');page.get_by_role('button',name='Audit').click()
    dialog=page.get_by_role('dialog',name='Task audit');dialog.wait_for()
    assert 'auto' in dialog.evaluate('(e)=>getComputedStyle(e).overflowY')
    assert dialog.evaluate('(e)=>e.scrollHeight>e.clientHeight')
    dialog.evaluate('(e)=>e.scrollTop=e.scrollHeight')
    assert dialog.evaluate('(e)=>e.scrollTop>0')
    table=dialog.locator('.audit-table-wrap')
    table.evaluate('(e)=>e.scrollTop=e.scrollHeight')
    assert table.evaluate('(e)=>e.scrollTop>0')
    assert dialog.get_by_text('0.00 credits').count()==1
    assert dialog.get_by_text('$0.0037 USD').count()==0 and 'Interview API cost' not in dialog.inner_text()
    page.screenshot(path=str(_screenshots_dir / 'audit-scroll-verified.png'),full_page=True)


def test_failed_needs_retry_selects_env_default_model_not_interview_model(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live['state']='failed';live['active_phase']='needs';live['model_route']='ollama:qwen2.5-coder:14b'
    for phase in live['phases']:
        if phase['phase_key'] in ('intent','conops'):phase['state']='approved'
        elif phase['phase_key']=='needs':phase.update({'state':'failed','detail':'Old generator failure'})
    live['input_state']={'waiting':False,'process_live':False,'engine_process_status':40}
    live['ui_state']={'mode':'error','active_phase':'needs','process_live':False}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/live/retry-needs',lambda route:route.fulfill(json={
        'status':'preview','phase':'needs','session_id':'complete-session','default_model':'ollama:phi4',
        'checkpoint':False,'last_failure':'Old generator failure','message':'Retry only Needs'}))
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[
        {'id':'ollama','available':True,'models':['qwen2.5-coder:14b','phi4']},
        {'id':'deepseek','available':True,'models':['deepseek-chat']}],'default_route':{'route':'ollama','model':'phi4'}}))
    page.goto(ui+'/tasks/live/view');page.locator('#retryneeds').click()
    dialog=page.get_by_role('dialog',name='Retry Stakeholder needs');dialog.wait_for()
    assert dialog.locator('#retrymodel').input_value()=='ollama:phi4'
    assert dialog.get_by_text('No generator checkpoint exists; Needs starts fresh.',exact=False).is_visible()
    assert 'qwen' not in dialog.locator('p').all_inner_texts()[0]
    page.screenshot(path=str(_screenshots_dir / 'needs-retry-model-selector.png'),full_page=True)

def test_phase_tooltip_does_not_present_old_errors_as_current_issues(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live['state']='running';live['active_phase']='architecture'
    for phase in live['phases']:
        if phase['phase_key']=='needs':phase['state']='approved'
        if phase['phase_key']=='architecture':phase.update({'state':'running','detail':'Waiting for architecture baseline'})
    live['events'].extend([
        {'id':980,'kind':'error','message':'old Needs checkpoint error','phase_key':'needs','payload':{},'ts':1720004000},
        {'id':981,'kind':'command','message':'Engine process started','phase_key':'needs','payload':{},'ts':1720004001},
        {'id':982,'kind':'error','message':'old architecture answer conflict','phase_key':'architecture','payload':{},'ts':1720004002},
        {'id':983,'kind':'founder_decision','message':'Choose architecture baseline','phase_key':'architecture','payload':{},'ts':1720004003},
    ])
    live['ui_state']={'mode':'error','active_phase':'architecture','process_live':False}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view')
    needs=page.locator('.phase-list-primary .phase').filter(has_text='Stakeholder needs');needs.hover()
    expect(needs.get_by_role('tooltip')).to_contain_text('No issues recorded.')
    architecture=page.locator('.phase-list-primary .phase').filter(has_text='Architecture');architecture.hover()
    expect(architecture.get_by_role('tooltip')).to_contain_text('No issues recorded.')
    expect(architecture.get_by_role('tooltip')).not_to_contain_text('old architecture answer conflict')
    expect(page.locator('.task-error-state')).not_to_contain_text('old architecture answer conflict')
    page.screenshot(path=str(_screenshots_dir / 'architecture-stale-issues-fixed.png'),full_page=True)


def test_normal_mode_shows_live_architecture_stage_and_stall_heartbeat(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live['state']='running';live['active_phase']='architecture';live['phases'][4].update({'state':'running','started_at':1720004000,'detail':'Drafting architecture overview'})
    live['events'].append({'id':990,'kind':'command','message':'Engine process started','phase_key':'architecture','payload':{},'ts':1720004000})
    live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('#stageprogress')).to_contain_text('Waiting for stage progress')
    assert page.locator('#progressfeed').count()==0
    progress={'id':991,'kind':'progress','message':'Drafting architecture overview (stage 3/6)','phase_key':'architecture','payload':{'completed':3,'total':6},'ts':1720004001}
    page.route('**/api/tasks/live/stream',lambda route:route.fulfill(status=200,headers={'Content-Type':'text/event-stream'},body='event: progress\ndata: '+__import__('json').dumps(progress)+'\n\n'+'event: progress\ndata: '+__import__('json').dumps({'id':992,'kind':'progress','message':"still working - stage 'architecture' call, 30s elapsed",'phase_key':'architecture','payload':{'completed':0,'total':None},'ts':1720004002})+'\n\n'))
    page.reload()
    expect(page.locator('#stageprogress')).to_contain_text('still working')
    expect(page.locator('.progress-scope')).to_contain_text('3 out of 6')
    page.screenshot(path=str(_screenshots_dir / 'architecture-progress-normal.png'),full_page=True)


def test_every_generator_gate_uses_same_page_and_configured_model_picker(page,ui):
    from delivery_api.phases import PHASE_DEFINITIONS
    live=page.request.get(ui+'/api/tasks/sim').json()
    expected=[phase.key for phase in PHASE_DEFINITIONS if phase.executor=='generator']
    assert [key for key,value in live['phase_config'].items() if value['executor']=='generator']==expected
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=_promote_control(live)))
    for phase in expected:
        page.goto(f'{ui}/tasks/sim/gates/gate-{phase}')
        expect(page.locator('.gate-page')).to_be_visible()
        expect(page.locator('#gate-artifacts')).to_be_visible()
        expect(page.locator('#gateform')).to_be_visible()
        page.locator('.model-panel summary').click()
        expect(page.locator('select[name="redo_model"]')).to_be_visible()
    for phase in ('intent','conops'):
        page.goto(f'{ui}/tasks/sim/gates/gate-{phase}')
        expect(page.locator('.gate-page')).to_be_visible()
        expect(page.locator('#gateform')).to_be_visible()
        expect(page.locator('select[name="redo_model"]')).to_have_count(0)


def test_cross_ui_waiting_banner_during_slow_write(page,ui):
    page.goto(ui+'/tasks/empty/view')
    page.get_by_role('group',name='Default model route').wait_for()
    page.evaluate("""() => {
      const original=window.fetch;
      window.fetch=(url,opts={})=>String(url).endsWith('/feedback')&&opts.method==='POST'
        ? new Promise(resolve=>setTimeout(()=>resolve(new Response('{}',{status:200,headers:{'content-type':'application/json'}})),1000))
        : original(url,opts);
      import('/assets/client-core.js').then(m=>m.api('/api/tasks/empty/feedback',{method:'POST',body:'{}'}));
    }""")
    page.locator('#server-wait:not(.hidden)').wait_for(timeout=3000)
    assert 'Saving feedback' in page.locator('#server-wait').inner_text()
    page.screenshot(path=str(_screenshots_dir / 'fast-follow-waiting-banner.png'),full_page=False)
    page.locator('#server-wait').wait_for(state='hidden',timeout=3000)


def test_background_refresh_stays_quiet_and_terminal_output_poll_stops(page, ui):
    # Drive the approved 60s safety callbacks explicitly; state events are the normal refresh path.
    # The background read must not flash the global wait banner, and once the environment
    # reports phase output as unavailable here (local preview), that poll leg must stop
    # while the snapshot liveness leg keeps running. Foreground reads must still show progress.
    page.add_init_script("""const originalInterval=window.setInterval;window.__safety=[];window.setInterval=(fn,ms,...args)=>{if(ms===60000)window.__safety.push(()=>fn(...args));return originalInterval(fn,ms,...args)}""")
    page.goto(ui + '/tasks/live/view')
    page.locator('.detail-head').wait_for()
    real = page.request.get(ui + '/api/tasks/live/phase-output').json()
    terminal = {**real, 'status': 'local_preview', 'items': []}
    hits = {'task': 0, 'output': 0}
    def slow_task(route):
        hits['task'] += 1
        page.wait_for_timeout(900)  # longer than the 350ms banner delay
        route.continue_()
    def terminal_output(route):
        hits['output'] += 1
        route.fulfill(json=terminal)
    page.evaluate("""() => {
      window.__bannerShows = 0;
      const banner = document.querySelector('#server-wait');
      new MutationObserver(() => {
        if (!banner.classList.contains('hidden')) window.__bannerShows++;
      }).observe(banner, {attributes: true, attributeFilter: ['class']});
    }""")
    page.route('**/api/tasks/live', slow_task)
    page.route('**/api/tasks/live/phase-output', terminal_output)
    assert page.evaluate('window.__safety.length')>=1
    for cycle in range(2):
        page.evaluate('window.__safety.forEach(fn=>fn())')
        page.wait_for_timeout(1200)  # allow the deliberate 900ms request to finish

    assert page.evaluate('window.__bannerShows') == 0, 'background refresh flashed the Loading task banner'
    assert hits['task'] >= 2, f"snapshot liveness stopped: {hits}"
    assert hits['output'] == 1, f"terminal output status did not stop the output poll: {hits}"
    assert 'not part of a local preview' in page.locator('#phase-output-slot').inner_text()
    # Foreground progress is preserved: a full reload issues a foreground task read, still delayed.
    page.evaluate('window.__bannerShows = 0')
    page.reload()
    page.wait_for_function("document.querySelector('#server-wait') && !document.querySelector('#server-wait').classList.contains('hidden')", timeout=5000)
    page.locator('.detail-head').wait_for()
    page.unroute('**/api/tasks/live', slow_task)
    page.unroute('**/api/tasks/live/phase-output', terminal_output)



def test_route_placeholder_holds_geometry_then_reveals_without_shell_shift(page, ui):
    # Delayed dashboard data: a labeled skeleton with reserved geometry fills the outlet
    # immediately, then real data reveals in place; the shell never moves.
    def slow(route):
        page.wait_for_timeout(1200)
        route.continue_()
    page.route('**/api/tasks', slow)
    page.goto(ui + '/about')
    page.locator('#app h1').wait_for()
    shell_y_before = page.locator('#shell-top').evaluate('e=>e.getBoundingClientRect().y')
    page.click('a[href="/"]')
    note = page.locator('.task-grid [role="status"]')
    note.wait_for()
    assert 'Loading tasks' in note.inner_text()
    assert page.locator('.task-grid[aria-busy="true"]').count() == 1
    skeletons = page.locator('.task-skeleton')
    assert skeletons.count() == 3
    height = skeletons.first.evaluate('e=>e.getBoundingClientRect().height')
    assert height >= 200, f'skeleton must reserve card geometry, got {height}'
    page.screenshot(path=str(_screenshots_dir / 'dashboard-loading-placeholder.png'), full_page=False)
    page.locator('.task[data-open]').first.wait_for()
    assert page.locator('.task-grid[aria-busy]').count() == 0
    assert page.locator('.task-skeleton').count() == 0
    assert page.evaluate("getComputedStyle(document.querySelector('.task-grid')).animationName") == 'requi-reveal'
    shell_y_after = page.locator('#shell-top').evaluate('e=>e.getBoundingClientRect().y')
    assert shell_y_before == shell_y_after, 'shell moved during placeholder-to-data transition'
    page.screenshot(path=str(_screenshots_dir / 'dashboard-revealed.png'), full_page=False)
    page.unroute('**/api/tasks', slow)


def test_awaiting_mount_shows_labeled_placeholder_not_blank(page, ui):
    # A route whose mount awaits before rendering (task detail) must show the labeled
    # router placeholder with reserved geometry, never a blank outlet.
    def slow(route):
        page.wait_for_timeout(1200)
        route.continue_()
    page.route('**/api/tasks/live', slow)
    page.goto(ui + '/about')
    page.locator('#app h1').wait_for()
    page.evaluate("location.href='/tasks/live/view'")
    placeholder = page.locator('.route-loading[role="status"]')
    placeholder.wait_for()
    assert placeholder.inner_text().strip() != ''
    height = placeholder.evaluate('e=>e.getBoundingClientRect().height')
    assert height >= 250, f'router placeholder must reserve geometry, got {height}'
    page.screenshot(path=str(_screenshots_dir / 'route-loading-placeholder.png'), full_page=False)
    page.locator('.detail-head').wait_for()
    assert page.locator('.route-loading').count() == 0
    page.unroute('**/api/tasks/live', slow)


def test_error_states_render_instantly_without_fade(page, ui):
    # Mount failure: the error modal appears immediately and the outlet does not fade.
    page.route('**/api/tasks/live', lambda route: route.fulfill(status=500, json={'error': 'boom'}))
    page.goto(ui + '/tasks/live/view')
    page.locator('#modal:not(.hidden)').wait_for()
    assert page.evaluate("document.querySelector('#app').classList.contains('view-in')") is False
    page.unroute('**/api/tasks/live')
    # In-view error state: the warning is committed at full opacity, never animated in.
    live = page.request.get(ui + '/api/tasks/live').json()
    live['state'] = 'running'; live['active_phase'] = 'intent'
    live['input_state'] = {'waiting': False, 'process_live': False, 'decision': None}
    live['ui_state'] = {'mode': 'error', 'active_phase': 'intent', 'process_live': False, 'decision': None, 'gate': None}
    live['events'].append({'id': 999, 'kind': 'error', 'message': 'Error: engine stream handling stopped.', 'phase_key': 'intent', 'payload': {}, 'ts': 1720003000})
    page.route('**/api/tasks/live', lambda route: route.fulfill(json=_promote_control(live)))
    page.goto(ui + '/tasks/live/view')
    notice = page.locator('.task-error-state')
    notice.wait_for()
    assert page.evaluate("document.querySelector('#app').classList.contains('view-in')") is False
    assert page.evaluate("getComputedStyle(document.querySelector('#app')).animationName") == 'none'
    assert notice.evaluate('e=>getComputedStyle(e).opacity') == '1'
    page.unroute('**/api/tasks/live')


def test_browser_session_refresh_single_flight_before_expiry(page,ui):
    import threading,time
    page.goto(ui+'/tasks/empty/view')
    page.get_by_role('group',name='Default model route').wait_for()
    calls=[]
    def refresh(route):
        calls.append(time.time())
        route.fulfill(status=200,json={'status':'refreshed','expires_at':time.time()+43200})
    page.route('**/auth/refresh',refresh)
    result=page.evaluate("""async () => {
      const m=await import('/assets/client-core.js');
      m.setSessionExpiry(Date.now()/1000+9*60);
      await Promise.all([m.renewSessionIfDue(),m.renewSessionIfDue()]);
      await m.renewSessionIfDue();
      return true;
    }""")
    assert result and len(calls)==1



def test_items_gate_accepts_list_artifact_and_reports_invalid_evidence(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json()
    gate=next(g for g in task['gates'] if g['after_phase']=='specs')
    gate.update({'state':'waiting','is_open':0})
    task['active_phase']='specs';task['state']='waiting_gate'
    item={'phase_key':'specs','name':'items.json','title':'Item Specifications','stale':False,
          'present':True,'valid':True,'validator':'ItemSpec[]','generation':1}
    task['artifacts']=[item]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=task))
    page.route('**/api/tasks/sim/artifacts/specs/items.json/view',lambda route:route.fulfill(json={
        'name':'items.json','title':'Item Specifications','phase':'specs','format':'json',
        'content':'[{"spec_id":"SPEC-1","title":"Status screen"}]'}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-specs')
    page.locator('#gate-artifacts .gate-structured').wait_for()
    assert page.locator('#gate-artifacts .gate-artifact-summary').count()==0
    assert 'Status screen' in page.locator('#gate-artifacts .gate-structured').inner_text()
    choose=page.locator('[data-action="approve"]')
    assert choose.is_enabled()
    expect(page.locator('#decision-checklist')).to_contain_text('Ready to continue')
    assert page.locator('.model-panel').get_attribute('open') is None  # Model panel starts closed
    assert page.locator('select[name="selected_option"]').count()==0  # 'Choose another option' recorded nothing, so the pills are gone
    assert page.locator('input[name="selected_option"]').input_value().startswith('Approve ')
    page.set_viewport_size({'width':1249,'height':900});page.screenshot(path=str(_screenshots_dir / 'decision-panel-closed.png'))
    page.locator('.model-panel summary').click();page.locator('#gateform').screenshot(path=str(_screenshots_dir / 'decision-panel-model-open.png'))
    page.screenshot(path=str(_screenshots_dir / 'specs-gate-ready.png'),full_page=True)
    sent=[]
    page.route('**/api/tasks/sim/gates/gate-specs/decide',lambda route:(sent.append(route.request.post_data_json),route.fulfill(json={'state':'decided'})))
    choose.click()
    page.wait_for_url('**/tasks/sim/view')
    assert len(sent)==1 and sent[0]['action']=='approve' and sent[0]['expected_revision']==gate['revision']
    # The same real artifact must not be silently hidden if it fails validation.
    item['valid']=False;item['validation_errors']=['1 validation error for list[ItemSpec]: invalid item']
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-specs')
    notice=page.locator('.gate-evidence-error');notice.wait_for()
    page.screenshot(path=str(_screenshots_dir / 'specs-gate-invalid.png'),full_page=True)
    assert 'Error: The current artifact failed validation.' in notice.inner_text()
    assert 'Likely cause: items.json:' in notice.inner_text()
    assert 'Next: Check the task artifact' in notice.inner_text()
    assert not choose.is_enabled()
    assert float(choose.evaluate('(b)=>getComputedStyle(b).opacity')) < 1


def test_accepted_answer_disarms_old_question_until_new_control_arrives(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'old-input','precondition':{'session_sequence':7},'prompt':'Old question?'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    sent=[]
    page.route('**/api/tasks/live/answer',lambda route:(sent.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');form=page.locator('#answerform');box=form.locator('textarea');button=form.locator('button[type=submit]')
    box.fill('The accepted answer');button.click()
    page.get_by_text('Answer sent. Waiting for the next interview step',exact=False).wait_for()
    assert box.is_disabled() and button.is_disabled() and page.locator('#currentquestion').is_hidden()
    assert form.get_attribute('data-awaiting-next')=='true'
    assert sent==[{'answer':'The accepted answer','decision_id':'old-input','expected_sequence':7}]
    page.wait_for_timeout(200)
    assert box.is_disabled() and button.is_disabled() and len(sent)==1
    live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'text','wait_id':'new-input','precondition':{'session_sequence':9},'prompt':'New question?'}
    page.reload();page.locator('#currentquestion').get_by_text('New question?').wait_for()
    assert page.locator('#answerform textarea').is_enabled() and page.locator('#answerform button[type=submit]').is_enabled()


def test_gate_model_picker_shows_exact_sticky_default(page,ui):
    page.route('**/api/tasks/sim/generator-default/needs',lambda route:route.fulfill(json={'phase':'needs','model':'deepseek:deepseek-flash','source':'selected'}))
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-conops')
    page.locator('.model-panel summary').click()
    picker=page.locator('select[name="next_model"]');picker.wait_for()
    expect(page.locator('[data-model-for="next_model"]')).to_contain_text('Current task default: deepseek:deepseek-flash')
    assert picker.locator('option').first.inner_text()=='Use current task default'
    page.screenshot(path=str(_screenshots_dir / 'sticky-model-gate.png'),full_page=True)

def _done_task(page,ui):
    task=page.request.get(ui+'/api/tasks/done').json();task=_promote_control(task)
    base=1791127000.0
    for i,p in enumerate(task['phases']):p['started_at']=base+i*60;p['completed_at']=base+i*60+50
    task['artifacts']=[{'phase_key':'srs','name':'srs.json','present':True,'stale':0},{'phase_key':'specs','name':'items.json','present':True,'stale':0}]
    task['events']=[{'kind':'price','phase_key':'needs','payload':{'price':0.625,'price_usd':0.63,'tokens':50000,'rates':{'scale':'credits'}}},{'kind':'price','phase_key':'srs','payload':{'price':900000,'tokens':450000,'rates':{'price_per_token':2.0}}}]
    page.route('**/api/tasks/done',lambda route:route.fulfill(json=task))
    page.route('**/api/tasks/done/audit',lambda route:route.fulfill(json={'actor_mode':'basic','entries':[],'totals':{'tokens':50000,'price':0.625,'price_usd':0.63,'legacy_priced_phases':1}}))
    page.route('**/api/tasks/done/artifacts/srs/srs.json',lambda route:route.fulfill(json={'functional':[{'requirement_id':'R1'},{'requirement_id':'R2'}],'non_functional':[{'requirement_id':'N1'}]}))
    page.route('**/api/tasks/done/artifacts/specs/items.json',lambda route:route.fulfill(json=[{'spec_id':'S1'}]))
    return task


def test_done_task_redirects_once_to_completion_screen(page,ui):
    _done_task(page,ui)
    page.goto(ui+'/about');page.evaluate("localStorage.setItem('allow-complete-redirect','1');sessionStorage.clear()")
    page.goto(ui+'/tasks/done/view');page.wait_for_url('**/tasks/done/complete')
    page.get_by_role('link',name='Back to task').click();page.wait_for_url('**/tasks/done/view')
    page.get_by_role('heading',name='Project complete').wait_for()
    assert page.url.endswith('/tasks/done/view')


def test_completion_screen_summary_download_and_package(page,ui):
    _done_task(page,ui)
    page.route('**/api/tasks/done/packages',lambda route:route.fulfill(json={'status':'ready','generation':1,'package_id':'a'*64,'url':'/api/tasks/done/packages/'+'a'*64+'?generation=1','contents':['README.md','spec/AGENTS.md']}))
    page.goto(ui+'/tasks/done/complete')
    page.get_by_role('heading',name='Congratulations',exact=False).wait_for()
    assert page.locator('.confetti i').count()==60
    body=page.locator('.complete-page').inner_text()
    assert '0.63 credits · $0.63' in body and 'legacy scale' in body and 'tokens' not in body.lower()
    counts=page.locator('.complete-counts')
    assert counts.locator('div').nth(0).inner_text().replace('\n',' ').lower()=='requirements 3' and counts.locator('div').nth(1).inner_text().replace('\n',' ').lower()=='specs 1' and counts.locator('div').nth(2).inner_text().replace('\n',' ').lower()=='tests -'
    assert counts.get_by_role('link',name='3').get_attribute('href')=='/tasks/done/gates/gate-srs'
    assert page.get_by_role('link',name='Download artifacts').get_attribute('href')=='/api/tasks/done/artifacts.zip'
    page.get_by_role('button',name='Prepare final package').click()
    link=page.get_by_role('link',name='Download coding-agent package');expect(link).to_be_visible();assert '?generation=1' in link.get_attribute('href')
    page.screenshot(path=str(_screenshots_dir / 'completion-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(_screenshots_dir / 'completion-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


def _answer_page(page, ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live = page.request.get(ui + '/api/tasks/live').json(); live['active_phase'] = 'intent'
    live['control_snapshot'] = {'mode': 'waiting_input', 'owner': 'engine', 'kind': 'text', 'wait_id': 'test-input', 'precondition': {'session_sequence': 1}}
    live['ui_state'] = {'mode': 'engine_question', 'active_phase': 'intent', 'process_live': True, 'decision': None, 'gate': None, 'question': 'What matters most?'}
    page.route('**/api/tasks/live', lambda route: route.fulfill(json=_promote_control(live)))


def test_submitted_answer_stays_visible_as_shadow_copy_while_processing(page, ui):
    _answer_page(page, ui)
    page.route('**/api/tasks/live/answer', lambda route: route.fulfill(json={'status': 'accepted'}))
    page.goto(ui + '/tasks/live/view'); answer = page.locator('#answerform textarea')
    answer.fill('Speed, then cost'); answer.press('Enter')
    page.get_by_text('Processing your answer', exact=False).wait_for()
    shadow = page.locator('#shadowcopy')
    assert shadow.is_visible()
    assert page.locator('#shadowq').inner_text() == 'What matters most?'
    assert page.locator('#shadowa').inner_text() == 'Speed, then cost'
    assert page.locator('#answerform textarea').is_hidden() and page.locator('#answerform button[type=submit]').is_hidden()
    page.screenshot(path=str(_screenshots_dir / 'shadow-copy.png'))
    # a re-render while the server is still working keeps the shadow copy
    page.reload(); page.locator('#shadowcopy').wait_for(state='visible')
    assert page.locator('#shadowa').inner_text() == 'Speed, then cost' and page.locator('#answerform textarea').is_hidden()


def test_shadow_copy_not_shown_when_send_fails(page, ui):
    _answer_page(page, ui)
    page.route('**/api/tasks/live/answer', lambda route: route.fulfill(status=503, json={'detail': {'error': 'Answer could not be sent.', 'cause': 'x', 'next': 'y'}}))
    page.goto(ui + '/tasks/live/view'); answer = page.locator('#answerform textarea')
    answer.fill('Keep me'); page.locator('#answerform button[type=submit]').click()
    page.get_by_text('Answer could not be sent.', exact=False).wait_for()
    assert page.locator('#shadowcopy').is_hidden() and page.locator('#answerform').is_visible()
    assert answer.input_value() == 'Keep me' and page.locator('#currentquestion').is_visible()


def _long_interview_page(page, ui, answered=True):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live = page.request.get(ui + '/api/tasks/live').json(); live['active_phase'] = 'intent'; live['state'] = 'running'
    events = list(live.get('events') or [])
    if answered:
        for i in range(14):
            events.append({'kind': 'waiting_input', 'phase_key': 'intent', 'message': f'Question {i}?', 'payload': {'question': f'Question {i}?'}})
            events.append({'kind': 'answer', 'phase_key': 'intent', 'message': f'You: Answer {i} ' + 'long text ' * 20, 'payload': {'answer': f'Answer {i} ' + 'long text ' * 20}})
    live['events'] = events
    live['control_snapshot'] = {'mode': 'waiting_input', 'owner': 'engine', 'kind': 'text', 'wait_id': 'test-input', 'precondition': {'session_sequence': 1}}
    live['ui_state'] = {'mode': 'engine_question', 'active_phase': 'intent', 'process_live': True, 'decision': None, 'gate': None, 'question': 'What next?'}
    page.route('**/api/tasks/live', lambda route: route.fulfill(json=_promote_control(live)))
    page.route('**/api/tasks/live/phase-output', lambda route: route.fulfill(json={'phase_key': 'intent', 'display_phase': 'intent', 'generation': live['generation'], 'status': 'waiting', 'items': [], 'source': 'intent'}))
    page.set_viewport_size({'width': 1400, 'height': 800})
    page.goto(ui + '/tasks/live/view')


def test_start_outputs_box_hidden_once_first_answer_exists(page, ui):
    _long_interview_page(page, ui, answered=True)
    page.locator('#phase-output-slot').wait_for(state='attached')
    page.wait_for_timeout(500)
    assert page.get_by_text('Start outputs').count() == 0 or page.get_by_text('Start outputs').first.is_hidden()
    assert page.locator('#phase-output-slot').get_attribute('data-hide-start') == '1'


def test_start_outputs_box_shown_before_first_answer(page, ui):
    _long_interview_page(page, ui, answered=False)
    page.get_by_text('Start outputs').first.wait_for(state='visible')


def test_interview_scrolls_inside_its_section_and_live_process_stays_visible(page, ui):
    _long_interview_page(page, ui, answered=True)
    left = page.locator('section.qa-history'); left.wait_for(); page.wait_for_timeout(500)
    assert left.evaluate('e=>e.scrollHeight>e.clientHeight') and left.evaluate('e=>getComputedStyle(e).overflowY') == 'auto'
    assert left.evaluate('e=>e.scrollTop')==0  # newest exchange is first, so it opens at the top
    assert 'Question 13?' in left.locator('.qa-item').first.inner_text() and 'Question 0?' in left.locator('.qa-item').last.inner_text()
    right = page.get_by_role('heading', name='Live process').locator('xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," panel ")][1]')
    # 14 long exchanges must not stretch the page: the measured end state is ~1.9x the 800px
    # viewport (1500px reference, 1516px Linux sandbox), so 2x bounds layout explosions
    # without depending on absolute font pixels.
    inner=page.evaluate('window.innerHeight');total=page.evaluate('document.documentElement.scrollHeight')
    assert total<=inner*2,f'page stretched to {total}px on a {inner}px viewport'
    page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)'); page.wait_for_timeout(300)
    box = right.bounding_box()
    heading = page.get_by_role('heading', name='Live process'); hb = heading.bounding_box()
    assert 0 <= hb['y'] and hb['y'] + hb['height'] <= 800, hb  # Live process heading pinned in view after the page scrolled
    assert heading.evaluate('e=>{const r=e.getBoundingClientRect();const t=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e.contains(t)||t.contains(e)}')  # and not hidden behind the page header
    page.screenshot(path=str(_screenshots_dir / 'live-process-sticky.png'))


QQ_DEFAULTS={"retrieval":{"max_words":90,"max_chars":600,"top_k":4},"verification":{"retry_enabled":False,"grounding_overlap_min":1},
             "integrations":{"none_stated_enabled":True,"external_system_verbs":["connect","sync"]},"area_cues":{"data":["from","own"]}}


def test_debug_dialog_edits_question_quality_settings(page, ui):
    state={"config":json.loads(json.dumps(QQ_DEFAULTS)),"puts":[]}
    def handler(route):
        req=route.request
        if req.method=="GET":return route.fulfill(json={"config_version":1,"config":state["config"],"defaults":QQ_DEFAULTS})
        body=json.loads(req.post_data)["config"];state["puts"].append(body)
        if body["retrieval"]["max_words"]>300:return route.fulfill(status=422,json={"detail":{"error":"retrieval.max_words must be an integer from 20 to 300","cause":"The engine validates every setting before saving.","next":"Fix that setting and save again. Nothing was changed."}})
        state["config"]=body;route.fulfill(json={"config_version":1,"config":body})
    page.route("**/api/question-quality-config",handler)
    page.goto(ui+"/");page.locator("#accountbutton").click();page.locator("#opendebug").click();page.locator(".debug-dialog").wait_for()
    page.get_by_text("Question quality settings",exact=True).click()
    words=page.locator("#qq-retrieval-max_words");words.wait_for();assert words.input_value()=="90"
    assert page.locator("#qq-verification-retry_enabled").is_checked() is False
    assert page.locator("#qq-integrations-external_system_verbs").input_value()=="connect, sync"
    page.screenshot(path="/tmp/d1-debug-qq.png");page.locator("#qqsave").scroll_into_view_if_needed();page.screenshot(path="/tmp/d1-debug-qq2.png")
    page.locator("#qq-verification-retry_enabled").check();words.fill("60");page.locator("#qq-area_cues-data").fill("from, own, bundled")
    page.locator("#qqsave").click();expect(page.locator("#qqstatus")).to_contain_text("Saved")
    assert state["puts"][-1]["retrieval"]["max_words"]==60 and state["puts"][-1]["verification"]["retry_enabled"] is True
    assert state["puts"][-1]["area_cues"]["data"]==["from","own","bundled"]
    words.fill("999");page.locator("#qqsave").click();expect(page.locator("#qqstatus")).to_contain_text("retrieval.max_words")
    assert state["config"]["retrieval"]["max_words"]==60
    page.locator("#qqreset").click();assert page.locator("#qq-retrieval-max_words").input_value()=="90"
    assert state["config"]["retrieval"]["max_words"]==60  # reset only fills the form; nothing is saved until Save
    page.get_by_role("button",name="Close debug").click()


def test_interview_history_lists_newest_exchange_first_keeping_original_numbers(page, ui):
    route_task_snapshot(page, ui, "live", interview_history_snapshot)
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Interview so far").wait_for()
    items = page.locator(".qa-item")
    assert items.count() == 2
    assert items.nth(0).locator(".qa-question p").inner_text() == "Who else should weigh in?"
    assert items.nth(0).locator(".qa-tag").first.inner_text() == "Q2"      # numbers stay chronological
    assert items.nth(1).locator(".qa-question p").inner_text() == "What should the family see first?"
    assert items.nth(1).locator(".qa-tag").first.inner_text() == "Q1"
    # each pair keeps its question above its answer
    assert items.nth(0).locator(".qa-answer p").inner_text() == "My co-founder, next week"


def test_submitting_answer_is_prominent_and_pulses_unless_reduced_motion(page, ui):
    _answer_page(page, ui)
    def slow(route):
        page.wait_for_timeout(1500); route.fulfill(json={'status': 'accepted'})
    page.route('**/api/tasks/live/answer', slow)
    page.goto(ui + '/tasks/live/view'); answer = page.locator('#answerform textarea')
    answer.fill('Speed, then cost'); answer.press('Enter')
    banner = page.locator('#server-wait')
    page.wait_for_function("document.querySelector('#server-wait') && !document.querySelector('#server-wait').classList.contains('hidden')")
    assert banner.inner_text().strip() == 'Submitting answer' and 'answering' in banner.get_attribute('class')
    size = banner.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')
    assert size >= 16 and banner.evaluate('e=>getComputedStyle(e).animationName') == 'answer-pulse'
    assert banner.evaluate('e=>parseFloat(getComputedStyle(e).borderTopWidth)') >= 3
    page.screenshot(path=str(_screenshots_dir / 'submitting-answer.png'))
    page.get_by_text('Processing your answer', exact=False).wait_for()
    shadow = page.locator('#shadowcopy')
    assert shadow.evaluate('e=>getComputedStyle(e).animationName') == 'answer-pulse'
    assert shadow.evaluate('e=>parseFloat(getComputedStyle(e).borderTopWidth)') >= 3
    page.screenshot(path=str(_screenshots_dir / 'processing-answer.png'))
    page.emulate_media(reduced_motion='reduce')
    assert shadow.evaluate('e=>getComputedStyle(e).animationName') == 'none'
    assert banner.evaluate('e=>getComputedStyle(e).animationName') == 'none'


def _phase_snapshots(page, ui, task_id="live"):
    base = page.request.get(f"{ui}/api/tasks/{task_id}").json()
    def snap(phase, mode):
        s = json.loads(json.dumps(base))
        s["state"] = "running"; s["active_phase"] = phase
        s["ui_state"] = {"mode": mode, "active_phase": phase, "process_live": True, "decision": None, "gate": None,
                         "question": "What next?" if mode == "engine_question" else None}
        if mode == "engine_question":
            s["control_snapshot"] = {"mode": "waiting_input", "owner": "engine", "kind": "text", "wait_id": "w1", "precondition": {"session_sequence": 1}}
        else:
            s["control_snapshot"] = {"mode": "working"}
        s["events"] = []
        return _promote_control(s)
    return snap


FAKE_SSE = """class FakeEventSource{constructor(){window.__es=this;this.h={}}addEventListener(n,f){this.h[n]=f}close(){}}
window.EventSource=FakeEventSource;window.__emit=e=>window.__es.h.progress({data:JSON.stringify(e)})"""


def test_phase_change_dialog_explains_pauses_input_and_keeps_progress_underneath(page, ui):
    import json as _j
    page.add_init_script(FAKE_SSE)
    snap = _phase_snapshots(page, ui)
    cur = {"s": snap("conops", "executing")}
    page.route("**/api/tasks/live", lambda route: route.fulfill(json=cur["s"]))
    page.set_viewport_size({"width": 1320, "height": 870})
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Live process").first.wait_for()
    assert page.locator(".phase-change-dialog").count() == 0          # first look at a phase is not a change
    cur["s"] = snap("needs", "executing")                              # the engine moves to the next phase
    page.evaluate("window.__emit({id:2147483647,kind:'command',phase_key:'needs',message:'Snapshot changed'})")
    dialog = page.locator(".phase-change-dialog")
    dialog.wait_for(timeout=12000)
    text = dialog.inner_text()
    assert "Moving from Concept of operations to Stakeholder needs" in text
    assert "PHASE 3 OF 8" in text and "INPUT PAUSED" in text.upper()
    assert "Organizing your answers into stakeholder needs." in text
    assert "Your input is paused" in text
    page.evaluate("window.__emit({id:2147483647,kind:'progress',phase_key:'needs',message:'Drafting need 2 of 6'})")
    assert "Latest: Drafting need 2 of 6" in page.locator("#transitionprogress").inner_text()
    # the page underneath keeps receiving the same progress signals
    assert "Drafting need 2 of 6" in page.locator("#stageprogress").inner_text()
    assert dialog.evaluate("e=>getComputedStyle(e).animationName") == "answer-pulse"
    page.screenshot(path=str(_screenshots_dir / "phase-change-dialog.png"))
    page.emulate_media(reduced_motion="reduce")
    assert dialog.evaluate("e=>getComputedStyle(e).animationName") == "none"
    page.get_by_role("button", name="Keep watching in the background").click()
    assert page.locator(".phase-change-dialog").count() == 0
    assert "Drafting need 2 of 6" in page.locator("#stageprogress").inner_text()


def test_phase_change_dialog_closes_when_the_founder_can_act_again_and_never_shows_for_a_question(page, ui):
    page.add_init_script(FAKE_SSE)
    snap = _phase_snapshots(page, ui)
    cur = {"s": snap("conops", "executing")}
    page.route("**/api/tasks/live", lambda route: route.fulfill(json=cur["s"]))
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Live process").first.wait_for()
    cur["s"] = snap("needs", "executing")
    page.evaluate("window.__emit({id:2147483647,kind:'command',phase_key:'needs',message:'Snapshot changed'})")
    page.locator(".phase-change-dialog").wait_for(timeout=12000)
    cur["s"] = snap("needs", "engine_question")                        # input is possible again
    page.evaluate("window.__emit({id:2147483647,kind:'command',phase_key:'needs',message:'Snapshot changed'})")
    page.wait_for_function("!document.querySelector('.phase-change-dialog')", timeout=12000)
    # a phase change that arrives together with a question is not a paused state: no dialog
    cur["s"] = snap("srs", "engine_question")
    page.evaluate("window.__emit({id:2147483647,kind:'command',phase_key:'needs',message:'Snapshot changed'})")
    page.wait_for_timeout(6500)
    assert page.locator(".phase-change-dialog").count() == 0


def test_flagged_inputs_show_readable_item_and_no_circular_link(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['after_phase']=='srs');gate.update({'state':'waiting','is_open':0})
    task['artifacts']=[{'phase_key':'srs','name':'srs.json','title':'Requirements','stale':False,'present':True,'generation':1}]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=task))
    page.route('**/api/tasks/sim/artifacts/srs/srs.json/view',lambda route:route.fulfill(json={'name':'srs.json','title':'Requirements','phase':'srs','format':'markdown','content':'# Requirements\n\nText.'}))
    orig={'requirement_id':'R-11','title':'Ear training','statement':'The app shall play a clip.','priority':'must','acceptance':[{'criterion_id':'AC-54','statement':'A clip is played.'}]}
    sem={'findings':[{'item_id':'R-11','kind':'semantic','reason':'Found: Ear-training, a name the interview never used.','original':orig}],'decisions':[],'items':[{'item_id':'R-11','original':orig}]}
    mock_gate_review(page,ui,'sim',semantic=sem)
    page.goto(ui+'/tasks/sim/gates/gate-srs')
    panel=page.locator('#semantic-panel');panel.locator('article').wait_for()
    assert panel.locator('article h3 a').count()==0
    expect(page.locator('#decision-checklist')).to_contain_text('1 flagged input still needs a decision')
    assert not page.locator('[data-action="approve"]').is_enabled()
    page.locator('#decision-checklist [data-jump]').click()
    page.screenshot(path=str(_screenshots_dir / 'gate-flagged-checklist.png'),full_page=True)
    assert panel.locator('article pre').count()==0
    assert '{' not in panel.locator('article details').inner_text()
    assert 'The app shall play a clip.' in panel.locator('article details').inner_text()
    assert 'A clip is played.' in panel.locator('article details').inner_text()
    # Replace asks for readable fields, not JSON
    assert page.locator('#semantic-panel textarea[name^="semantic-replacement"]').count()==0
    assert page.locator('#semantic-panel .replace-fields').first.is_hidden()
    panel.locator('select[name="semantic-action-0"]').select_option('replace',force=True) if panel.locator('select[name="semantic-action-0"]').is_visible() else panel.locator('label.pill',has_text='Replace').first.click()
    assert page.locator('#semantic-panel .replace-fields input[value="Ear training"]').count()==1
    assert 'R-11' not in panel.locator('article').inner_text() and 'AC-54' not in panel.locator('article').inner_text()  # IDs hidden from founders
    assert page.locator('#semantic-panel .replace-fields input[readonly]').count()==0


def test_failed_architecture_has_a_retry_that_resumes_the_phase(page,ui):
    from playwright.sync_api import expect
    live=page.request.get(ui+'/api/tasks/live').json()
    for phase in live['phases']:
        if phase['phase_key'] in ('intent','conops','needs','srs'):phase['state']='approved'
        elif phase['phase_key']=='architecture':phase.update({'state':'failed','detail':'SRS candidate review is not ready.'})
        else:phase['state']='pending'
    live.update({'state':'failed','active_phase':'architecture','input_state':{'waiting':False,'process_live':False,'engine_process_status':40},
        'ui_state':{'mode':'error','active_phase':'architecture','process_live':False}})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[{'id':'deepseek','models':['deepseek-flash'],'available':True}]}))
    page.route('**/api/tasks/live/execution*',lambda route:route.fulfill(json={'session_id':'complete-session','route':'ollama','usage':[],'calls':[],'usage_segments':[],'raw':[]}))
    sent=[]
    def retry(route):
        body=route.request.post_data_json;sent.append(body)
        route.fulfill(json={'status':'preview','phase':'architecture','label':'Architecture','session_id':'complete-session','retains':['intent','conops','needs','srs'],'default_model':'deepseek:deepseek-flash','default_source':'selected','checkpoint':True,'last_failure':'SRS candidate review is not ready.','message':'Retry Architecture from the approved earlier phases. This may make new model calls and incur cost.'} if not body['confirm'] else {'status':'started','phase':'architecture','session_id':'complete-session'})
    page.route('**/api/tasks/live/retry-phase',retry)
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('#retryphase')).to_have_text('Retry Architecture')
    expect(page.locator('#retryneeds')).to_have_count(0)
    page.locator('#retryphase').click()
    dialog=page.get_by_role('dialog',name='Retry Architecture');expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('SRS candidate review is not ready.')
    expect(dialog).to_contain_text('Existing generator checkpoint will resume')
    page.screenshot(path=str(_screenshots_dir / 'failed-architecture-retry.png'),full_page=True)
    dialog.get_by_role('button',name='Retry phase').click()
    page.wait_for_function('()=>true')
    assert sent[0]=={'phase':'architecture','confirm':False}
    assert sent[1]=={'phase':'architecture','confirm':True,'model_route':'deepseek','model':'deepseek-flash'}



def test_running_cost_header_shows_credits_and_usd_and_grows_while_running(page,ui):
    page.goto(ui+"/tasks/live/view")
    header=page.locator("#sessioncost")
    header.wait_for(state="attached")
    # the header can restart (Loading again) while sibling widgets settle; expect() polls to the
    # settled exact text instead of catching a transient frame.
    expect(header).to_have_text("1.25 credits · $1.25",timeout=10000)
    assert "token" not in page.locator(".detail-head").inner_text().lower()
    page.screenshot(path=str(_screenshots_dir / "running-cost-header.png"),clip={"x":0,"y":0,"width":1365,"height":260})
    # visibilitychange is the app's own "back on the tab" refresh trigger, so dispatching it proves
    # the open page pulls new cost data without a reload. The mock returns the current stage value,
    # so extra refreshes are harmless; each dispatch is wrapped in the phase-breakdown response and
    # a short settle so the next one cannot land inside refresh()'s pending window and be dropped.
    stage={"price":2.5}
    page.route("**/api/tasks/live/cost",lambda route:route.fulfill(json={"price":stage["price"],"price_usd":stage["price"]}))
    page.wait_for_timeout(500)   # let the initial refresh clear its pending flag first
    with page.expect_response("**/api/tasks/live/cost-phases"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(header).to_have_text("2.50 credits · $2.50",timeout=10000)
    page.wait_for_timeout(200)
    stage["price"]=3.75
    with page.expect_response("**/api/tasks/live/cost-phases"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(header).to_have_text("3.75 credits · $3.75",timeout=10000)
def test_live_headline_names_latest_stage_and_stage_line_ignores_phase_tag(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live['state']='running';live['active_phase']='intent'
    live['ui_state']={'mode':'executing','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    live['events']=[{'id':1,'kind':'command','message':'Engine process started','phase_key':'intent','payload':{},'ts':time.time()-60}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('.live-indicator')).to_contain_text('Working · Start')
    expect(page.locator('#stageprogress')).to_contain_text('Waiting for stage progress')
    live['events'].append({'id':2,'kind':'progress','message':'Building CONOPS concept option 2/2 field disadvantages','phase_key':'conops','payload':{'label':'Building CONOPS concept option 2/2','stage':'conops'},'ts':time.time()-5})
    page.reload()
    expect(page.locator('.live-indicator')).to_contain_text('Working · Building CONOPS concept option 2/2')
    expect(page.locator('.live-indicator')).not_to_contain_text('Start')
    expect(page.locator('#stageprogress')).to_contain_text('Building CONOPS concept option 2/2 field disadvantages')
    live['events'].append({'id':3,'kind':'heartbeat','message':'Model call still running, 340s elapsed','phase_key':'intent','payload':{'label':'Building CONOPS concept option 2/2','stage':'conops','status':'heartbeat'},'ts':time.time()-1})
    page.reload()
    expect(page.locator('.live-indicator')).to_contain_text('Working · Building CONOPS concept option 2/2')
    page.screenshot(path=str(_screenshots_dir / 'live-stage-headline.png'),clip={'x':0,'y':100,'width':1365,'height':560})
def review_answer_snapshot(snapshot):
    raw = ('{"revision":"d5b1a909f522f0af0fbf7bd47f163993827bbf5d2dbfb32820","decisions":['
           '{"item_id":"constraint:0","action":"accept","rationale":""},'
           '{"item_id":"constraint:1","action":"remove","rationale":""}]}')
    snapshot["events"] = [
        {"id": 92001, "kind": "waiting_input", "phase_key": "intent", "ts": 1,
         "message": "Review these interview findings before continuing.",
         "payload": {"waiting_input": True, "question": "Review these interview findings before continuing."}},
        {"id": 92002, "kind": "answer", "phase_key": "intent", "ts": 2,
         "message": "You: " + raw, "payload": {"answer": raw}},
        {"id": 92003, "kind": "waiting_input", "phase_key": "intent", "ts": 3,
         "message": "Second question", "payload": {"waiting_input": True, "question": "Second question"}},
        {"id": 92004, "kind": "answer", "phase_key": "intent", "ts": 4,
         "message": "You: Reviewed 1 flagged item:\nRemoved - Constraint: Must be a web app",
         "payload": {"answer": "{}", "answer_display": "Reviewed 1 flagged item:\nRemoved - Constraint: Must be a web app"}},
    ]


def test_interview_feed_shows_flagged_item_review_as_words_not_json(page, ui):
    route_task_snapshot(page, ui, "live", review_answer_snapshot)
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Interview so far").wait_for()
    joined = "\n".join(page.locator(".qa-answer").all_inner_texts())
    page.locator(".qa-item").first.screenshot(path="/tmp/review-bubble.png")
    assert "Reviewed 2 flagged items" in joined and "kept as written: Constraint 1" in joined and "removed: Constraint 2" in joined
    assert "Removed - Constraint: Must be a web app" in joined
    assert "revision" not in joined and "item_id" not in joined and "d5b1a909" not in joined and "{" not in joined


@pytest.mark.parametrize("width", [1365, 390])
def test_live_heartbeat_wraps_without_clipping(page, ui, width):
    page.set_viewport_size({"width": width, "height": 900})
    live = page.request.get(ui + "/api/tasks/live").json()
    live["state"] = "running"
    live["active_phase"] = "intent"
    live["ui_state"] = {"mode": "executing", "active_phase": "intent", "process_live": True,
                        "decision": None, "gate": None}
    text = "Model call still running, 340s elapsed. Waiting for the current response before continuing to the next stage."
    live["events"] = [{"id": 1, "kind": "command", "phase_key": "intent", "message": "Started", "ts": time.time()-60, "payload": {}},
                      {"id": 2, "kind": "heartbeat", "phase_key": "intent", "message": text, "ts": time.time(), "payload": {}}]
    page.route("**/api/tasks/live", lambda route: route.fulfill(json=_promote_control(live)))
    page.goto(ui + "/tasks/live/view")
    page.locator("#stageprogress > details > summary").click()
    line = page.locator("#stageprogress .heartbeat span")
    expect(line).to_have_text(text)
    assert line.evaluate("e => getComputedStyle(e).whiteSpace") == "normal"
    assert line.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    assert line.evaluate("e => e.scrollHeight <= e.clientHeight + 1")
    page.locator(".live-status-row").screenshot(path=str(_screenshots_dir / f"heartbeat-wrap-{width}.png"))


@pytest.mark.parametrize('path', ['/tasks/live/view','/tasks/live/gates/gate-conops','/tasks/done/complete','/','/about'])
def test_global_cost_header_on_all_route_families(page,ui,path):
 base=ui
 task=path.startswith('/tasks/')
 page.route('**/api/tasks/*/cost',lambda r:r.fulfill(json={'price':.73,'price_usd':.73}))
 page.goto(base+path)
 host=page.locator('header #sessioncost')
 expect(host).to_have_text('0.73 credits · $0.73' if task else 'No Session Selected')
 assert page.locator('#sessioncost').count()==1
 for width in (1365,390):
  page.set_viewport_size({'width':width,'height':844});page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(200)
  expect(host).to_be_in_viewport()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.evaluate('window.scrollTo(0,700)');page.wait_for_timeout(200)
  head=page.locator('header').bounding_box();assert head['y']==0
  for box in page.locator('.page-head').all():
   if box.evaluate('e=>getComputedStyle(e).position')=='sticky':assert box.bounding_box()['y']>=head['height']-1
  name=path.strip('/').replace('/','-') or 'home'
  page.screenshot(path=f'/tmp/cost-route-{name}-{width}.png')


def test_closed_gate_can_save_revision_proposal_without_restarting(page,ui):
    task=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in task['gates'] if g['gate_key']=='gate-needs');gate['state']='decided'
    data={'review':{'revision':'gate:approved','task':task,'documents':[]},'actionable':False,'draft':None,'comments':[],'closed_revision_draft':True}
    page.route('**/api/tasks/sim/gates/gate-needs/review*',lambda r:r.fulfill(json=data));sent=[]
    page.route('**/api/tasks/sim/gates/gate-needs/revision-drafts',lambda r:(sent.append(r.request.post_data_json),r.fulfill(json={'draft_id':'fake','committed':False,'dispatch_started':False})))
    page.goto(ui+'/tasks/sim/gates/gate-needs');page.locator('#closedrevisiontext').fill('Proposed new requirement wording')
    page.locator('#saveclosedrevision').click()
    expect(page.locator('#closedrevisionstatus')).to_contain_text('unchanged')
    assert sent==[{'review_revision':'gate:approved','edited_text':'Proposed new requirement wording'}]
    expect(page.locator('[data-action="approve"]')).to_be_disabled()
    page.screenshot(path=str(SCREENSHOTS/'closed-gate-proposal-desktop.png'),full_page=True)


def test_a_session_unavailable_in_this_environment_is_described_only_as_what_is_known(page,ui):
    now=time.time()
    live=page.request.get(ui+"/api/tasks/live").json()
    live["state"]="running";live["active_phase"]="intent"
    live["control_snapshot"]={"version":2,"task_id":"live","phase_key":"intent","mode":"failed","owner":"none","wait_id":None,"kind":None,"prompt":None,"options":[],
        "artifact_ref":None,"actions":[],"precondition":{},"revision":"task:1","process_live":False,"decision":None,"gate":None,"input_id":None,
        "diagnostic":{"reason":"engine protocol rejected the snapshot","http_status":404,"code":120,"detail":"session not found","task_state":"running","active_phase":"intent"}}
    live["events"]=[{"id":1,"kind":"command","phase_key":"intent","message":"Started","ts":now-170000,"payload":{}},
                    {"id":2,"kind":"waiting_input","phase_key":"intent","message":"Which concept should the architecture execute?","ts":now-123831,"payload":{}}]
    page.route("**/api/tasks/live",lambda route:route.fulfill(json=live))
    page.goto(ui+"/tasks/live/view")
    page.locator(".live-status-row").wait_for()
    body=page.locator("main, #app, body").first.inner_text()
    assert "Session unavailable in this environment" in body and "needs attention" in body.lower()
    assert "Last recorded state: waiting for your answer at" in body and "no active process reported" in body
    assert "environment that owns this session" in body and "original deployment" in body and "can still change live data" in body
    assert "Connected to progress" not in body and "s elapsed" not in body and "so far" not in body and "in progress" not in body.lower() and "Waiting for stage progress" not in body and "Next:" not in body
    for banned in ("earlier stages","cannot resume","lost","nothing running","Engine returned an error"):
        assert banned not in body, banned
    assert page.get_by_role("button",name="Rerun stage").count()==1          # existing control, wording untouched
    page.screenshot(path=str(_screenshots_dir / "session-unavailable.png"),full_page=True)


def test_everything_zip_downloads_with_the_manifest_first(page,ui,tmp_path):
    import zipfile
    page.goto(ui+"/tasks/live/view");page.locator(".detail-head").wait_for()
    page.locator(".avatar").click();page.locator("#opendebug").click();page.locator(".debug-dialog").wait_for()
    with page.expect_download() as info:
        page.locator("#downloadeverything").click()
    download=info.value
    assert download.suggested_filename=="requi-everything.zip"
    target=tmp_path/"everything.zip";download.save_as(str(target))
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None and z.namelist()==["requi-everything.txt"]
        text=z.read("requi-everything.txt").decode("utf-8")
    assert text.startswith("===== Known omissions =====") and "Engine process logs are included only as a bounded, scrubbed recent tail" in text
    assert "Debug info" in text and "Client logs" in text and len(text)>200
    expect(page.locator("#debugstatus")).to_contain_text("requi-everything.zip")
    page.screenshot(path=str(_screenshots_dir / "everything-zip.png"),full_page=True)


def test_start_route_pills_show_all_choices_and_disabled_reasons(page,ui):
    routes={'managed':True,'default_route':{'route':'ollama'},'routes':[
        {'id':'deepseek','managed':True,'available':False,'error':'missing key: DEEPSEEK_API_KEY is not set on this server'},
        {'id':'ollama','managed':True,'available':True,'model':'ollama:phi4'}]}
    page.route('**/api/model-routes',lambda route:route.fulfill(json=routes))
    page.goto(ui+'/tasks/empty/view')
    choices=page.get_by_role('group',name='Default model route');choices.wait_for()
    assert choices.get_by_role('button',name='DeepSeek').is_disabled()
    assert 'DEEPSEEK_API_KEY' in choices.inner_text()
    assert 'Billed to your credits' in choices.inner_text()
    ollama=choices.get_by_role('button',name='Ollama');assert ollama.get_attribute('aria-pressed')=='true'
    assert 'ollama:phi4' in choices.inner_text()
    assert page.get_by_role('button',name='Start interview').is_enabled()
    page.screenshot(path='/downloads/start-route-options.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert choices.evaluate('e=>e.scrollWidth<=e.clientWidth')
    page.screenshot(path='/downloads/start-route-options-mobile.png',full_page=True)
    routes['routes'][1]={'id':'ollama','managed':True,'available':False,'error':'DELIVERY_LOCAL_PREVIEW is not enabled on this server'};routes['default_route']={'route':'deepseek'}
    page.reload();choices.wait_for()
    assert choices.get_by_role('button').count()==2
    assert 'DELIVERY_LOCAL_PREVIEW' in choices.inner_text()
    assert choices.locator('[aria-pressed=true]').count()==0
    assert not page.get_by_role('button',name='Start interview').is_enabled()


def test_idle_stream_reads_only_at_60s_safety_then_refreshes_on_events(page,ui):
    from collections import Counter
    page.clock.install()
    page.add_init_script("""window.__stream=null;window.EventSource=class {constructor(){this.readyState=1;this.listeners={};window.__stream=this}addEventListener(k,f){this.listeners[k]=f}close(){this.readyState=2}}""")
    counts=Counter()
    page.on('request',lambda request:counts.update([__import__('urllib.parse',fromlist=['urlparse']).urlparse(request.url).path]))
    page.route('**/api/account/credits',lambda r:r.fulfill(json={'credits':10}))
    page.route('**/api/tasks/live/cost',lambda r:r.fulfill(json={'price':1.25,'price_usd':1.25}))
    page.route('**/api/tasks/live/cost-phases',lambda r:r.fulfill(json={'phases':[{'phase':'intent','credits':1.25}]}))
    page.goto(ui+'/tasks/live/view');page.locator('#stageprogress').wait_for();expect(page.locator('#sessioncost')).to_have_text('1.25 credits · $1.25');page.wait_for_timeout(1500)
    for _ in range(10):
        page.clock.run_for(100);time.sleep(.1)
        if page.locator('#sessioncost').get_attribute('title').endswith('Total: 1.25 credits'):break
    assert page.locator('#sessioncost').get_attribute('title').endswith('Total: 1.25 credits')
    page.clock.pause_at(page.evaluate('Date.now()'))
    keys=['/api/account/credits','/api/tasks/live/cost','/api/tasks/live/cost-phases','/api/tasks/live','/api/tasks/live/phase-output']
    for _ in range(20):page.clock.run_for(100);time.sleep(.05)
    before={k:counts[k] for k in keys}
    page.clock.run_for(57000);time.sleep(.5);page.clock.run_for(100)
    idle={k:counts[k]-before[k] for k in keys}
    print('IDLE_57S_REQUESTS',idle)
    assert idle==dict.fromkeys(keys,0)
    page.clock.run_for(4000);time.sleep(.5);page.clock.run_for(100);time.sleep(.5);page.clock.run_for(100)
    safety={k:counts[k]-before[k] for k in keys}
    print('SAFETY_61S_REQUESTS',safety)
    assert all(v==1 for v in safety.values())
    page.evaluate("window.__stream.listeners.progress({data:JSON.stringify({id:999,kind:'progress',phase_key:'conops',message:'call done',payload:{stage:'llm_call:probing',status:'completed'}})})")
    page.clock.run_for(500);time.sleep(.5);page.clock.run_for(100);time.sleep(.5);page.clock.run_for(100)
    for _ in range(20):
        if counts['/api/tasks/live/cost']==before['/api/tasks/live/cost']+2:break
        page.clock.run_for(100);time.sleep(.1)
    assert counts['/api/tasks/live/cost']==before['/api/tasks/live/cost']+2
    assert counts['/api/tasks/live']==before['/api/tasks/live']+1, 'model call completion must not fetch task state'

@pytest.mark.parametrize('phase',['intent','needs'])
@pytest.mark.parametrize('width',[1365,390])
def test_active_phase_line_replaces_and_progress_stays_collapsed_until_expanded(page,ui,phase,width):
    page.set_viewport_size({'width':width,'height':844})
    live=page.request.get(ui+'/api/tasks/live').json()
    live['state']='running';live['active_phase']=phase
    live['ui_state']={'mode':'executing','active_phase':phase,'process_live':True,'decision':None,'gate':None}
    for row in live['phases']:
        row['state']='running' if row['phase_key']==phase else 'pending'
    live['events']=[{'id':100,'kind':'command','phase_key':phase,'message':'Started','ts':time.time()-20},
        {'id':101,'kind':'progress','phase_key':phase,'message':'Reading source inputs','ts':time.time()-5,
         'payload':{'stage':'inputs','label':'Reading source inputs','status':'started'}}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.add_init_script("""window.EventSource=class{constructor(){window.liveSource=this;this.listeners={};setTimeout(()=>this.onopen?.(),0)}addEventListener(k,f){this.listeners[k]=f}close(){}emit(e){this.listeners.progress({data:JSON.stringify(e)})}}""")
    page.goto(ui+'/tasks/live/view')
    line=page.locator('.phase.running .phase-current-progress')
    expect(line).to_have_text('Reading source inputs')
    box=page.locator('#stageprogress > details')
    expect(box).not_to_have_attribute('open','')
    expect(box.locator(':scope > summary')).to_contain_text('Connected to progress')
    expect(box.locator('.progress-history')).not_to_be_visible()
    page.evaluate('liveSource.onerror()')
    expect(box.locator(':scope > summary')).to_contain_text('Progress connection lost')
    page.evaluate('liveSource.onopen()')
    expect(box.locator(':scope > summary')).to_contain_text('Connected to progress')
    page.evaluate("liveSource.emit({id:102,kind:'progress',phase_key:"+json.dumps(phase)+",message:'Drafting the next item',payload:{stage:'draft',status:'started'},ts:Date.now()/1000})")
    expect(line).to_have_text('Drafting the next item')
    expect(box).not_to_have_attribute('open','')
    box.locator(':scope > summary').click()
    expect(box.locator('.progress-history')).to_be_visible()
    page.evaluate("liveSource.emit({id:103,kind:'progress',phase_key:"+json.dumps(phase)+",message:'Checking the result',payload:{stage:'check',status:'started'},ts:Date.now()/1000})")
    expect(line).to_have_text('Checking the result')
    expect(box).to_have_attribute('open','')
    page.screenshot(path=str(_screenshots_dir / ('compact-progress-'+phase+'-'+str(width)+'-expanded.png')),full_page=True)
    # A state event that changes a sibling phase forces a full snapshot render.
    sibling=next(row for row in live['phases'] if row['phase_key']!=phase)
    sibling['state']='approved'
    page.evaluate("liveSource.emit({id:104,kind:'command',phase_key:"+json.dumps(phase)+",message:'Refresh state',payload:{},ts:Date.now()/1000})")
    expect(page.locator('.phase.approved')).to_have_count(1)
    expect(box).to_have_attribute('open','')
    page.evaluate("liveSource.emit({id:104,kind:'progress',phase_key:"+json.dumps(phase)+",message:'Another result',payload:{stage:'check',status:'completed'},ts:Date.now()/1000})")
    expect(box).to_have_attribute('open','')
    box.locator(':scope > summary').click()
    page.screenshot(path=str(_screenshots_dir / ('compact-progress-'+phase+'-'+str(width)+'-collapsed.png')),full_page=True)

def test_waiting_phase_status_is_not_overwritten_by_late_heartbeat(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live['active_phase']='intent';live['state']='running';live['control_snapshot']={'kind':'text'}
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'question':'What should it do?','decision':None,'gate':None,'show_question':True,'show_answer_composer':True}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)))
    page.add_init_script("window.EventSource=class{constructor(){window.liveSource=this;this.listeners={}}addEventListener(k,f){this.listeners[k]=f}close(){}emit(e){this.listeners.progress({data:JSON.stringify(e)})}}")
    page.goto(ui+'/tasks/live/view')
    line=page.locator('.phase-current-progress')
    expect(line).to_have_text('Waiting for your answer')
    page.evaluate("liveSource.emit({id:10001,kind:'heartbeat',phase_key:'intent',message:'Old working heartbeat',payload:{stage:'probe',status:'heartbeat'},ts:Date.now()/1000})")
    expect(line).to_have_text('Waiting for your answer')
    expect(page.locator('#stageprogress > details > summary')).to_contain_text('Waiting for your answer')


def test_both_admitted_routes_can_be_selected_without_hiding_either(page,ui):
    routes={'managed':True,'default_route':{'route':'deepseek'},'routes':[
        {'id':'deepseek','managed':True,'available':True,'model':'deepseek:deepseek-flash'},
        {'id':'ollama','managed':True,'available':True,'model':'ollama:phi4'}]}
    page.route('**/api/model-routes',lambda route:route.fulfill(json=routes))
    page.goto(ui+'/tasks/empty/view')
    choices=page.get_by_role('group',name='Default model route')
    for name in ['DeepSeek','Ollama','DeepSeek']:
        button=choices.get_by_role('button',name=name)
        expect(button).to_be_enabled();button.click()
        expect(button).to_have_attribute('aria-pressed','true')
        expect(choices.locator('[aria-pressed=true]')).to_have_count(1)
    expect(page.locator('#defaultroute')).to_have_value('deepseek')
    page.screenshot(path='/downloads/both-provider-options.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert choices.evaluate('e=>e.scrollWidth<=e.clientWidth')
    expect(choices.get_by_role('button',name='Ollama')).to_be_enabled()
    page.screenshot(path='/downloads/both-provider-options-mobile.png',full_page=True)


def test_customer_audit_summary_is_credits_only_even_with_legacy_provider_totals(page,ui):
    page.route('**/api/tasks/live/audit',lambda route:route.fulfill(json={'actor_mode':'basic','entries':[],
        'totals':{'price':2.5,'price_usd':2.5,'interview_calls':14,'interview_cost_usd':.0037}}))
    page.goto(ui+'/tasks/live/view');page.get_by_role('button',name='Audit').click()
    dialog=page.get_by_role('dialog',name='Task audit')
    expect(dialog).to_contain_text('Credits Used')
    expect(dialog).to_contain_text('2.50 credits')
    for text in ['Interview API cost','No usage records','USD','$']:
        expect(dialog).not_to_contain_text(text)
    page.screenshot(path='/downloads/audit-credits-only.png',full_page=True)


def test_gate_comment_shared_guard_prevents_duplicate_and_allows_retry(page,ui):
    route_task_snapshot(page,ui,'sim',waiting_gate_snapshot)
    mock_gate_review(page,ui,'sim')
    pending=[]
    page.route('**/api/tasks/sim/gates/*/comments',lambda route:pending.append(route))
    page.goto(ui+'/tasks/sim/gates/gate-intent');page.locator('#gateform').wait_for()
    page.locator('#gateform [name=comment]').fill('Only one comment')
    page.locator('#gate-save-comment').click()
    page.wait_for_timeout(100)
    assert len(pending)==1
    assert page.locator('#gate-save-comment').get_attribute('aria-busy')=='true'
    page.locator('#gate-save-comment').dispatch_event('click');page.wait_for_timeout(100)
    assert len(pending)==1
    pending.pop().fulfill(status=500,json={'detail':'retry fixture'})
    expect(page.locator('#gate-decision-status')).to_contain_text('retry fixture')
    expect(page.locator('#gate-save-comment')).to_be_enabled()
    page.locator('#gate-save-comment').click();page.wait_for_timeout(100)
    assert len(pending)==1
    pending.pop().fulfill(json={'comment_id':1})
    expect(page.locator('#gate-comments')).to_contain_text('Only one comment')
    assert page.locator('#gate-save-comment').get_attribute('aria-busy') is None


def test_gate_approval_shared_guard_ignores_second_action_and_preserves_retry(page,ui):
    def with_evidence(task):
        waiting_gate_snapshot(task)
        task['artifacts']=[{'phase_key':'intent','name':'intent_model.json','title':'Needs and Scope','stale':False,'present':True,'valid':True,'generation':1}]
    route_task_snapshot(page,ui,'sim',with_evidence)
    page.route('**/api/tasks/sim/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'title':'Needs and Scope','format':'json','content':'{}'}))
    mock_gate_review(page,ui,'sim')
    pending=[]
    page.route('**/api/tasks/sim/gates/*/decide',lambda route:pending.append(route))
    page.goto(ui+'/tasks/sim/gates/gate-intent');page.locator('#gateform').wait_for()
    page.locator('[data-action="approve"]').click();page.wait_for_timeout(100)
    assert len(pending)==1
    assert page.locator('[data-action="approve"]').get_attribute('aria-busy')=='true'
    page.locator('[data-action="approve"]').dispatch_event('click');page.wait_for_timeout(100)
    assert len(pending)==1
    page.screenshot(path='/downloads/gate-shared-action-busy.png',full_page=True)
    pending.pop().fulfill(status=500,json={'detail':'approval retry fixture'})
    expect(page.locator('#gate-decision-status')).to_contain_text('approval retry fixture')
    expect(page.locator('[data-action="approve"]')).to_be_enabled()
    page.locator('[data-action="approve"]').click();page.wait_for_timeout(100)
    assert len(pending)==1
    pending.pop().fulfill(status=409,json={'detail':'stale review fixture'})
    expect(page.locator('#gate-decision-status')).to_contain_text('stale review fixture')
    expect(page.locator('[data-action="approve"]')).to_be_disabled()


def test_pg_dashboard_fixture_requires_signed_workspace():
    import inspect
    assert 'pg_browser' in inspect.signature(pg_ui).parameters


def test_artifact_browser_group_requires_signed_pg_fixture():
    import inspect
    for case in (test_completed_artifacts_have_singleton_tooltips_viewer_and_downloads,
                 test_artifact_viewer_uses_shared_dialog_shell_and_local_close,
                 test_artifact_viewer_shows_json_artifacts_readably,
                 test_audit_uses_shared_dialog_sortable_filterable_sticky_table,
                 test_artifact_viewer_renders_content_title_bold_and_transcript):
        assert 'pg_page' in inspect.signature(case).parameters


def test_history_feedback_browser_group_requires_signed_pg_fixture():
    import inspect
    for case in (test_interview_history_truncates_questions_and_answers_after_32_words,
                 test_feedback_thumbs_comment_persist_and_delete,
                 test_feedback_save_disables_while_pending_and_closes_only_on_success,
                 test_question_comment_columns_follow_character_ratio):
        assert 'pg_page' in inspect.signature(case).parameters
