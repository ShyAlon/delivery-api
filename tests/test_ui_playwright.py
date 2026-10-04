"""Browser-level contracts for the three task views and traceable rerun UX."""
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
    env = {**os.environ, "DELIVERY_DB_PATH": str(db),"VMODEL_ARTIFACTS_DIR":str(artifacts),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    seed = '''
from delivery_api.store import SQLiteStore
import os
s=SQLiteStore(os.environ["DELIVERY_DB_PATH"])
s.create_task("Empty definition", "Awaiting its first run", task_id="empty")
s.create_task("Live definition", "Detailed progress", task_id="live")
s.set_model_route("live", "ollama/phi4")
s.update_phase("live", "intent", "done", detail="Approved")
s.update_phase("live", "conops", "running", detail="Option 2/3", completed=2, total=3)
s.add_event("live", "progress", "Generating CONOPS option 2/3", phase_key="conops")
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
        yield page
        browser.close()

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


def test_dashboard_draws_v_and_all_task_states(page, ui):
    page.goto(ui)
    page.locator(".task").first.wait_for()
    assert page.locator(".task").count() == 4
    assert page.locator(".v-map svg").count() == 4
    # Home is task-only. Session bundles retain their own /sessions route.
    assert page.locator('.hero,.home-sessions,.session-card').count()==0
    page.get_by_role('link',name='All sessions').click()
    partial=page.locator('.session-card',has_text='Partial discovery evidence')
    partial.wait_for()
    assert partial.locator('.session-v .status-done').count()==1
    assert partial.locator('.session-v .status-not_started').count()==7
    page.goto(ui)
    page.locator('.task').first.wait_for()
    assert page.get_by_text("In progress").count() == 1
    assert page.get_by_text("Complete").count() == 1


def test_require_brand_is_consistent(page, ui):
    page.goto(ui)
    assert page.title() == "Requi.re / Product definition workspace"
    brand=page.locator('.brand-name')
    assert brand.inner_text().startswith('Requi.re')
    assert brand.locator('strong').inner_text()=='.re'
    assert page.locator('body').inner_text().lower().find('vector') == -1
    page.goto(ui+'/about')
    page.get_by_text('About Requi.re',exact=True).wait_for();assert page.get_by_text('About Requi.re',exact=True).is_visible()
    assert page.locator('body').inner_text().lower().find('vector') == -1


def test_not_started_panel_layout(page,ui):
    page.goto(ui+'/tasks/empty/view')
    page.locator('#defaultroute').wait_for()
    start=page.get_by_role('button',name='Start interview')
    panel=page.locator('.not-started');route=panel.locator('#defaultroute');label=panel.locator('.start-route');
    assert panel.evaluate("e=>getComputedStyle(e).borderRadius")=='28px'
    assert panel.locator('.start-controls').count()==1
    assert label.inner_text().startswith('DEFAULT MODEL ROUTE')
    control=route.bounding_box();action=start.bounding_box();assert control['x']+control['width']+12<action['x']
    assert control['y']>=label.bounding_box()['y']+12
    assert start.is_visible() and not start.is_enabled()
    assert 'cannot start:' in panel.locator('#startstatus').inner_text()


def test_start_screen_displays_engine_resolved_model_and_conflict(page, ui):
    routes={'routes':[{'id':'ollama','available':True,'model':'ollama:phi4'},
                      {'id':'deepseek','available':False,'error':"selected route 'deepseek' conflicts with INTERVIEW_ENGINE_MODEL_PROBING='ollama:phi4'"}]}
    page.route('**/api/model-routes',lambda route:route.fulfill(json=routes))
    page.goto(ui+'/tasks/empty/view')
    selector=page.locator('#defaultroute');selector.wait_for()
    assert 'ollama:phi4' in selector.locator('option[value=ollama]').inner_text()
    assert not page.get_by_role('button',name='Start interview').is_enabled()
    assert 'INTERVIEW_ENGINE_MODEL_PROBING' in page.locator('#startstatus').inner_text()
    selector.select_option('ollama')
    assert page.get_by_role('button',name='Start interview').is_enabled()
    page.screenshot(path="/downloads/interview-model-preview.png",full_page=True)


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

def test_basic_auth_browser_signup_signin_persists_and_logs(tmp_path):
    db=tmp_path/"basic-ui.db";port=_port();log=Path(__file__).parents[1]/"run.log"
    env={**os.environ,"DELIVERY_DB_PATH":str(db),"DELIVERY_BASIC_AUTH_ENABLED":"1","DELIVERY_COOKIE_SECURE":"0","PYTHONPATH":str(Path(__file__).parents[1]/"src")}
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","delivery_api.app:app","--host","127.0.0.1","--port",str(port)],env=env);base=f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:import urllib.request;urllib.request.urlopen(base+"/health",timeout=.2);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(base+"/sessions")
            page.get_by_text("Welcome to Requi.re",exact=True).wait_for()
            page.evaluate("window.__guardToken=Math.random()")
            page.get_by_role("link",name="All sessions").click()
            assert page.evaluate("window.__guardToken") is not None and page.url==base+"/sessions"
            page.get_by_role("button",name="Create account").click()
            email=page.locator('input[name="username"]');email.fill('partial');assert page.locator('#emailstatus').inner_text()==''
            email.blur();assert page.locator('#emailstatus').inner_text()=='Enter a complete email address.'
            email.fill('shy@example.com');page.get_by_text('Email is available.').wait_for()
            page.locator('input[name="password"]').fill('Strong local 42!');page.locator('.auth-submit').click()
            page.locator('.avatar').wait_for();assert page.url==base+'/'
            assert page.locator('.avatar').inner_text()=='SH'
            page.reload();page.locator('.avatar').wait_for();assert page.locator('.avatar').is_visible()
            page.get_by_role('link',name='All sessions').click();page.get_by_text('Artifacts folder').wait_for()
            browser.close()
        assert 'auth.signup.success' in log.read_text() and 'client.navigation' in log.read_text()
    finally:proc.terminate();proc.wait(timeout=5)

def test_oauth_identity_and_logout_in_browser(tmp_path):
    db=tmp_path/"oauth-ui.db";port=_port()
    env={**os.environ,"DELIVERY_DB_PATH":str(db),"DELIVERY_SESSION_SECRET":"0123456789abcdef0123456789abcdef",
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
    card=page.locator('.task',has_text='Empty definition')
    card.get_by_role('button',name='Rename').click()
    page.locator('#renameform input[name=title]').fill('Renamed empty definition')
    page.get_by_role('button',name='Save name').click()
    page.get_by_text('Renamed empty definition',exact=True).wait_for()
    card=page.locator('.task',has_text='Renamed empty definition')
    card.get_by_role('button',name='Delete').click()
    assert page.get_by_text('Original files in the artifacts folder stay on disk.').is_visible()
    page.get_by_role('button',name='Delete task').click()
    page.locator('#app').get_by_text('Renamed empty definition',exact=True).wait_for(state='detached')
    assert page.get_by_text('Renamed empty definition',exact=True).count()==0


def test_session_card_shows_live_brief_and_progress(page,ui):
    page.goto(ui+"/sessions")
    page.get_by_role('link',name='A founder brief for a live interview').wait_for()
    card=page.locator('.session-card',has=page.get_by_role('link',name='A founder brief for a live interview'))
    assert card.is_visible()
    assert card.locator('.status-in_progress').count()==1
    assert page.get_by_text('The workspace is ready. Starting opens an interview with the connected engine and saves your progress here.').count()==0


def test_session_discovery_bundle_and_trace_diagram(page,ui):
    page.add_init_script("""Object.defineProperty(navigator,'clipboard',{value:{_text:'',writeText(v){this._text=v;return Promise.resolve()},readText(){return Promise.resolve(this._text)}}})""")
    page.goto(ui+"/sessions")
    page.get_by_text("Family task evidence",exact=True).click()
    assert page.url.endswith("/sessions/golden-1/interview")
    page.get_by_text("Start").wait_for();assert page.get_by_text("Start").is_visible()
    assert not page.get_by_text("Stakeholder needs").is_visible()
    # Desktop phase navigation follows a compact V: left leg descends, right leg rises.
    pills={k:page.locator(f'[data-phase="{k}"]').bounding_box() for k in ("interview","needs","srs","architecture","specs","tests","reviews","oracle")}
    assert pills["interview"]["y"] < pills["needs"]["y"] < pills["srs"]["y"] < pills["architecture"]["y"] < pills["specs"]["y"]
    assert pills["specs"]["y"] > pills["tests"]["y"] > pills["reviews"]["y"] > pills["oracle"]["y"]
    assert pills["interview"]["x"] < pills["needs"]["x"] < pills["specs"]["x"] < pills["tests"]["x"] < pills["oracle"]["x"]
    assert page.locator(".bundle-panel .json-view").count()==0
    answer=page.locator('#interview-gates .inspect-json',has_text='Interviewee Answer')
    assert answer.is_visible();answer.click()
    dialog=page.get_by_role("dialog",name="Interviewee Answer");assert dialog.is_visible()
    dialog.get_by_role("button",name="Copy JSON").click()
    assert dialog.get_by_role("button",name="JSON copied").is_visible()
    assert '"confidence": "high"' in page.evaluate("navigator.clipboard.readText()")
    page.get_by_role("button",name="Close").click()
    assert page.locator('.phase-pill.status-done').count()==8
    for status in ("done","error","warning","not_started","in_progress"):
        assert page.locator(f'.phase-pill.status-{status}').evaluate_all("els=>els.every(e=>getComputedStyle(e).getPropertyValue('--phase-status').trim()!=='')")
    # Every pill is a real phase page, and route refresh is stable.
    page.evaluate("window.__spaToken=Math.random()")
    body_handle=page.evaluate("window.__spaToken")
    page.get_by_role("link",name="Needs ↗").click()
    assert page.url.endswith("/sessions/golden-1/needs")
    assert page.evaluate("window.__spaToken") == body_handle
    page.locator('[data-panel="interview"]').wait_for(state="hidden")
    assert page.locator('[data-panel="needs"]').evaluate("e=>({cls:e.className,path:location.pathname})")=={"cls":"bundle-panel","path":"/sessions/golden-1/needs"}
    assert not page.get_by_text("Start").is_visible()
    page.reload();assert page.get_by_role("link",name="Needs ↗").evaluate("e=>e.classList.contains('active')")
    page.get_by_role("link",name="SRS ↗").click()
    page.get_by_text("System requirements").wait_for();assert page.get_by_text("System requirements").is_visible()
    assert page.locator(".trace-node").count()==5
    node=page.locator('.trace-node[data-node="R-1"]')
    node.hover();tip=page.locator('#trace-tooltip');assert tip.is_visible()
    assert "R-1" in tip.inner_text() and "Visible status" in tip.inner_text()
    page.get_by_role("link",name="Architecture ↗").click()
    assert page.url.endswith("/sessions/golden-1/architecture")
    page.get_by_text("Build a PWA").wait_for();assert page.get_by_text("Build a PWA").is_visible()
    adr=page.locator('.inspect-item',has_text='ADR-1');adr.click()
    item_dialog=page.get_by_role("dialog",name="ADR 1 · Progressive Web App")
    item_dialog.get_by_text("Alternatives considered").wait_for();assert item_dialog.get_by_text("Alternatives considered").is_visible()
    for text in ("Native","Deeper device access","Two codebases","Outside first release"):
        assert item_dialog.get_by_text(text,exact=text=="Native").is_visible()
    assert item_dialog.locator('.json-view').count()==0
    page.get_by_role("button",name="Close").click()
    # Ordinary readable cards open their JSON result.
    page.get_by_text("Task service",exact=True).click()
    assert page.get_by_role("dialog",name="Task service").locator('.json-view').count()==1
    page.locator("#modal").click(position={"x":4,"y":4})
    # Remaining phase routes each render only their own content.
    expected={"specs":"Module specifications","tests":"Test specifications","reviews":"Gate decisions + review rounds","oracle":"Bundle integrity passed"}
    for route,title in expected.items():
        page.goto(f"{ui}/sessions/golden-1/{route}")
        page.get_by_text(title,exact=True).wait_for();assert page.get_by_text(title,exact=True).is_visible()
        assert page.get_by_role("link",name={"specs":"Module specs ↗","tests":"Tests ↗","reviews":"Reviews + gates ↗","oracle":"Oracle ↗"}[route]).evaluate("e=>e.classList.contains('active')")
    # Compact sticky header sits below global nav and content starts below it.
    head=page.locator('.detail-head.page-head');panel=page.locator('[data-panel="oracle"]')
    assert head.evaluate("e=>e.parentElement.classList.contains('bundle-workspace')")
    assert head.bounding_box()["height"] < 150
    assert panel.bounding_box()["y"] >= head.bounding_box()["y"]+head.bounding_box()["height"]
    page.screenshot(path="/downloads/vmodel-phase-page-v-nav.png",full_page=True)

def test_artifacts_folder_setting_validates_and_reports_sessions(page,ui):
    page.goto(ui+"/sessions");page.get_by_role("button",name="Artifacts folder").click()
    page.screenshot(path="/downloads/vmodel-artifacts-folder-setting.png",full_page=True)
    page.locator('input[name="path"]').fill('/definitely/missing/artifacts')
    page.get_by_role("button",name="Validate & load sessions").click()
    page.get_by_text("Artifacts folder could not be loaded.").wait_for()
    assert page.get_by_text("Likely cause:",exact=False).is_visible()

def test_dashboard_routes_local_loading_to_session_folder_not_file_picker(page,ui):
    page.goto(ui)
    page.get_by_role("link",name="All sessions").wait_for();assert page.get_by_role("link",name="All sessions").is_visible()
    assert page.get_by_role("button",name="Import run").count()==0
    page.get_by_role("link",name="All sessions").click()
    page.get_by_role("button",name="Artifacts folder").wait_for()

def test_page_headers_stay_sticky_and_sessions_have_no_evidence_vault_hero(page,ui):
    page.goto(ui+"/sessions")
    controls=page.locator('.session-controls.page-head');controls.wait_for()
    assert controls.evaluate("e=>getComputedStyle(e).position")=="sticky"
    assert page.get_by_text("Evidence vault / completed sessions",exact=True).count()==0
    assert page.get_by_text("Every decision,",exact=False).count()==0
    page.goto(ui+"/tasks/done/view")
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


def test_settings_creation_dialog_enter_and_escape(page,ui):
    page.goto(ui+'/sessions');page.get_by_role('button',name='Artifacts folder').click()
    path=page.locator('#rootform input[name="path"]');page.wait_for_function("document.activeElement?.name==='path'");assert path.evaluate('e=>document.activeElement===e')
    path.press('Escape');assert page.locator('#rootform').count()==0
    page.get_by_role('button',name='Artifacts folder').click();path=page.locator('#rootform input[name="path"]')
    submitted=[]
    page.route('**/api/settings/engine-python',lambda route:route.fulfill(json={'path':'','candidates':[],'count':0}) if route.request.method=='GET' else route.fulfill(json={'path':'','status':'saved'}))
    page.route('**/api/settings/artifacts-root',lambda route:(submitted.append(route.request.post_data_json),route.fulfill(status=400,json={'detail':{'error':'Artifacts folder could not be loaded.','cause':'Test path is missing.','next':'Choose an existing folder.'}})))
    path.fill('/definitely/missing/by-enter');path.press('Enter')
    page.get_by_text('Artifacts folder could not be loaded.',exact=False).wait_for();assert submitted==[{'path':'/definitely/missing/by-enter'}]

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
    page.evaluate("liveSource.emit({kind:'progress',message:'Still working',phase_key:'intent',payload:{status:'heartbeat',stage:'intent'},ts:1720000200})")
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
    page.screenshot(path='/downloads/interview-answer-sending.png',full_page=False)
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
    page.screenshot(path='/downloads/structured-founder-decision.png',full_page=True)
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
    page.screenshot(path='/downloads/restart-decision-aligned-panels.png',full_page=True)

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
    page.screenshot(path='/downloads/detail-layout-clean-1390x827.png',full_page=True)

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
    page.screenshot(path='/downloads/founder-dialog-vertical-four-options.png',full_page=True)

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
    page.screenshot(path='/downloads/decision-ready-sticky.png',full_page=False)

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
    page.screenshot(path='/downloads/typed-intent-approval.png',full_page=True)
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
    page.screenshot(path='/downloads/intent-approval-with-concept-reason.png',full_page=True)

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
    assert phases.count()==7 and page.locator('.detail-layout .v-map').count()==0 and page.locator('.detail-layout .gate-legend').count()==0
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
    page.screenshot(path='/downloads/managed-state-intent-decision.png',full_page=True)
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
    page.screenshot(path='/downloads/full-gate-requirements-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert evidence.locator('.artifact-readable').is_visible()
    page.screenshot(path='/downloads/full-gate-requirements-phone.png',full_page=True)


def test_gate_transition_reconciles_delayed_next_phase(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}close(){}}window.EventSource=FakeEventSource""")
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
    page.screenshot(path='/downloads/jev-d7-phase-transition-desktop.png',full_page=True)
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
    page.screenshot(path='/downloads/jev-d7-architecture-provenance-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path='/downloads/jev-d7-architecture-provenance-phone.png',full_page=True)

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
    page.screenshot(path='/downloads/gate-blocked-checklist.png',full_page=True)


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
    page.screenshot(path='/downloads/jev-d6-live-gate-evidence.png',full_page=True)


def test_gate_page_explains_controls_and_tooltips_desktop_and_phone(page,ui):
    live=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in live['gates'] if g['state']=='waiting');live['ui_state']={'mode':'phase_gate','active_phase':live['active_phase'],'process_live':False,'decision':None,'gate':gate}
    mock_gate_review(page,ui,'sim')
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/sim/gates/gate-intent');dialog=page.locator('#gateform');dialog.wait_for()
    for text in ['Approve moves on','Save edit records your wording','Ask for changes regenerates this phase']:
        assert dialog.get_by_text(text,exact=False).is_visible()
    page.locator('.model-panel summary').click()
    help_button=dialog.get_by_role('button',name='What does opening a gate do?');help_button.focus();tip=dialog.get_by_role('tooltip').filter(has_text='auto-approves when the phase arrives');expect(tip).to_be_visible()
    page.screenshot(path='/downloads/gate-help-desktop.png',full_page=True)
    mock_gate_review(page,ui,'sim')
    page.set_viewport_size({'width':390,'height':844});page.goto(ui+'/tasks/sim/gates/gate-intent');dialog=page.locator('#gateform');dialog.wait_for();page.locator('.model-panel summary').click();dialog.get_by_role('button',name='What does opening a gate do?').focus()
    tip=dialog.get_by_role('tooltip').filter(has_text='auto-approves when the phase arrives');expect(tip).to_be_visible();box=tip.bounding_box();assert box['x']>=0 and box['x']+box['width']<=390
    page.screenshot(path='/downloads/gate-help-phone.png',full_page=True)


def test_decision_dialog_rationale_help_is_real_and_keyboard_accessible(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'concept','title':'Choose one','prompt':'Which concept?','rationale_required':True,'options':[{'value':'1','label':'Native'},{'value':'2','label':'PWA'}]};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None};live['control_snapshot']={'mode':'waiting_input','owner':'engine','kind':'choice'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for();button=dialog.get_by_role('button',name='Why a rationale is requested');button.focus();expect(dialog.get_by_role('tooltip')).to_be_visible()
    expect(dialog.get_by_role('tooltip').get_by_text('stored with the selected option',exact=False)).to_be_visible()

def test_debug_dialog_contains_run_events_desktop_and_phone(page,ui):
    page.goto(ui+'/tasks/live/view');page.locator('.detail-layout').wait_for()
    assert page.locator('#console').count()==0
    page.locator('#accountbutton').click();page.locator('#opendebug').click();page.get_by_text('Run events',exact=True).click()
    page.locator('#debugexecution').get_by_text('Generating CONOPS option 2/3',exact=False).wait_for()
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
    page.screenshot(path='/downloads/live-scope-account-desktop.png',full_page=True);page.keyboard.press('Escape');assert menu.is_hidden()
    page.set_viewport_size({'width':390,'height':844});page.reload();scope.wait_for();page.locator('#accountbutton').click();menu.wait_for();page.screenshot(path='/downloads/live-scope-account-phone.png',full_page=True)

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
    page.screenshot(path='/downloads/missing-resource-recovery.png',full_page=True)

def test_copy_debug_info_includes_runtime_snapshot_and_resource_decision(page,ui):
    page.add_init_script("""window.__copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.__copied=text}})""")
    requirement={'kind':'external_input','title':'Baseline required','path':'/tmp/system-baseline.json','format':'SystemBaseline JSON'}
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='architecture';live['active_work']={'owner':'requirement','phase':'architecture','status':'waiting','started_at':time.time(),'durable_ref':requirement['path'],'requirement':requirement};live['input_state']={'waiting':True,'process_live':True,'decision':None,'requirement':requirement};live['control_snapshot']={'mode':'working'};live['ui_state']={'mode':'executing','active_phase':'architecture','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');page.locator('.requirement-dialog').wait_for();page.keyboard.press('Escape');page.locator('#accountbutton').click();page.get_by_role('menuitem',name='Debug',exact=True).click();button=page.get_by_role('button',name='Copy debug info');button.click();page.wait_for_function("window.__copied.length>0")
    copied=page.evaluate('window.__copied');assert json.loads(copied)['task_id']=='live';assert '"owner": "requirement"' in copied and '"detected": true' in copied and '"task_id": "live"' in copied

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

def test_progress_slot_keys_render_as_title_case(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='srs';live['events'].extend([{'id':980,'kind':'progress','message':'stack:pwa_offline_runtime · 12 tok','phase_key':'srs','payload':{},'ts':1720004000},{'id':981,'kind':'error','message':'Schema check found a missing acceptance criterion','phase_key':'srs','payload':{},'ts':1720004001}]);live['artifacts'].append({'phase_key':'srs','name':'srs.json','stale':0,'generation':1});live['ui_state']={'mode':'executing','active_phase':'srs','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');feed=page.locator('#stageprogress .progress-item');feed.wait_for()
    assert feed.locator('span').all_inner_texts()==['Stack: PWA Offline Runtime · 12 tok']
    assert feed.locator('span').get_attribute('title')=='Stack: PWA Offline Runtime · 12 tok'
    got=page.evaluate("""async()=>{const m=await import('/assets/format.js');return ['stack:ui_framework','stack:build_tooling','stack:client_persistence','component:audio_engine','plan: stack slots','R-12 statement'].map(m.humanizeSlotLabels)}""")
    assert got==['Stack: UI Framework','Stack: Build Tooling','Stack: Client Persistence','Component: Audio Engine','plan: stack slots','R-12 statement']

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

def test_progress_is_one_line_without_redundant_phase_and_phases_show_evidence_tooltips(page,ui):
    page.add_init_script("try{localStorage.setItem('requi-re.debug','1')}catch(e){}")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='srs';live['events'].extend([{'id':980,'kind':'progress','message':'A very long progress update that must remain on precisely one line while the full wording stays available on hover','phase_key':'srs','payload':{},'ts':1720004000},{'id':981,'kind':'error','message':'Schema check found a missing acceptance criterion','phase_key':'srs','payload':{},'ts':1720004001}]);live['artifacts'].append({'phase_key':'srs','name':'srs.json','stale':0,'generation':1});live['ui_state']={'mode':'executing','active_phase':'srs','process_live':True,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.goto(ui+'/tasks/live/view');feed=page.locator('#stageprogress .progress-item');feed.wait_for();span=feed.locator('span');assert feed.locator('small').count()==0;assert span.get_attribute('title').startswith('A very long progress');assert span.evaluate('(e)=>getComputedStyle(e).whiteSpace')=='nowrap';assert span.evaluate('(e)=>getComputedStyle(e).textOverflow')=='ellipsis'
    phase=page.locator('.phase-list-primary .phase').filter(has_text='Requirements');phase.hover();tip=phase.get_by_role('tooltip');tip.wait_for();assert tip.get_by_text('Artifacts: srs.json',exact=True).is_visible();assert tip.get_by_text('missing acceptance criterion',exact=False).is_visible()
    page.screenshot(path='/downloads/progress-phase-tooltips.png',full_page=True)


def test_dashboard_renders_existing_task_with_final_verification_gate(page,ui):
    base=page.request.get(ui+'/api/tasks/live').json();base['task_id']='daily';base['title']='Daily Habit';base['state']='waiting_gate';base['active_phase']='tests';base['phases'][-1]['state']='awaiting_approval';base['gates']=[g for g in base['gates'] if g.get('after_phase')!='tests'];base['gates'].append({'task_id':'daily','gate_key':'gate-tests','after_phase':'tests','label':'Verification gate','position':6,'is_open':0,'state':'waiting','options':['Approve Verification','Choose another option']})
    page.route('**/api/tasks',lambda route:route.fulfill(json={'tasks':[base]}))
    page.route('**/api/sessions',lambda route:route.fulfill(json={'sessions':[]}))
    page.goto(ui+'/');card=page.locator('.task',has_text='Daily Habit');card.wait_for();assert card.is_visible();assert card.locator('[data-gate="gate-tests"]').count()==1;assert page.get_by_text('Cannot read properties').count()==0

def test_completed_artifacts_have_singleton_tooltips_viewer_and_downloads(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='done';live['active_phase']='tests';live['phases'][-1]['state']='approved';live['artifacts']=[{'phase_key':'architecture','name':'architecture.json','ref':'/tmp/architecture.json','mime_type':'application/json','stale':False,'generation':4,'present':True,'valid':True,'validation_error':None}];live['ui_state']={'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.route('**/api/tasks/live/artifacts/architecture/architecture.json/view',lambda route:route.fulfill(json={'name':'architecture.json','phase':'architecture','format':'markdown','display_name':'architecture.md','content':'# Daily Habit Architecture\n\n- Progressive web app','raw_format':'json','raw_content':'{\n  "system": "Daily Habit"\n}'}));page.goto(ui+'/tasks/live/view');rows=page.locator('.phase-list-primary .phase');rows.nth(4).dispatch_event('mouseenter');assert rows.nth(4).locator('.phase-tooltip').is_visible();rows.nth(4).dispatch_event('mouseleave');rows.nth(5).dispatch_event('mouseenter');assert 'tip-open' not in (rows.nth(4).get_attribute('class') or '');assert 'tip-open' in (rows.nth(5).get_attribute('class') or '');assert page.locator('.download-all').is_visible();page.get_by_role('button',name='architecture.json').click();viewer=page.locator('.artifact-viewer');viewer.wait_for();assert viewer.get_by_role('heading',name='Daily Habit Architecture').is_visible();assert viewer.get_by_text('Progressive web app').is_visible();assert viewer.get_by_text('Show raw json').is_visible();assert viewer.get_by_role('link',name='Download').is_visible();page.screenshot(path='/downloads/completed-artifact-viewer.png',full_page=True)

def test_artifact_viewer_uses_shared_dialog_shell_and_local_close(page,ui):
    def mutate(live):live.update({'state':'done','artifacts':[{'phase_key':'intent','name':'intent_model.json','title':'Needs and Scope','stale':False,'generation':4,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}],'ui_state':{'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}})
    page.route('**/api/tasks/live/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'json','content':'{}'}));route_task_snapshot(page,ui,'live',mutate);page.goto(ui+'/tasks/live/view');card=page.locator('[data-view-artifact]').first;card.wait_for();assert card.get_by_text('Needs and Scope',exact=True).is_visible();card.click();shell=page.locator('.dialog-shell');viewer=page.get_by_role('dialog',name='Artifact viewer');viewer.wait_for();close=viewer.get_by_role('button',name='Close artifact viewer');assert close.is_visible();vb=viewer.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];assert cb['y']>=vb['y'] and cb['y']+cb['height']<=vb['y']+vb['height'];page.keyboard.press('Escape');assert shell.count()==0
    card.click();page.screenshot(path='/downloads/shared-artifact-dialog.png',full_page=True)

def test_artifact_viewer_shows_json_artifacts_readably(page,ui):
    def mutate(live):live.update({'state':'done','artifacts':[{'phase_key':'intent','name':'intent_model.json','title':'Needs and Scope','stale':False,'generation':4,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}],'ui_state':{'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}})
    page.route('**/api/tasks/live/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'json','content':'{"title":"Ear training","statement":"The app shall play a clip.","items":[{"name":"A clip"}]}'}));route_task_snapshot(page,ui,'live',mutate);page.goto(ui+'/tasks/live/view');card=page.locator('[data-view-artifact]').first;card.wait_for();assert card.get_by_text('Needs and Scope',exact=True).is_visible();card.click();shell=page.locator('.dialog-shell');viewer=page.get_by_role('dialog',name='Artifact viewer');viewer.wait_for();close=viewer.get_by_role('button',name='Close artifact viewer');assert close.is_visible();vb=viewer.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];assert cb['y']>=vb['y'] and cb['y']+cb['height']<=vb['y']+vb['height'];page.keyboard.press('Escape');assert shell.count()==0
    card.click();dlg=page.locator('.artifact-viewer');dlg.wait_for();assert dlg.locator('pre.artifact-content').count()==0;assert 'The app shall play a clip.' in dlg.inner_text() and '{' not in dlg.locator('.gate-structured').inner_text()

def test_audit_uses_shared_dialog_sortable_filterable_sticky_table(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));entries=[{'ts':1720000200,'actor':'Shy','action':'gate.approve','detail':{'phase':'srs'}},{'ts':1720000100,'actor':'policy','action':'gate.bypassed','detail':{'phase':'needs'}}];page.route('**/api/tasks/live/audit',lambda route:route.fulfill(json={'actor_mode':'basic','entries':entries,'totals':{'tokens':1234,'price':2501,'phases_priced':2}}));page.goto(ui+'/tasks/live/view');page.get_by_role('button',name='Audit').click();dialog=page.get_by_role('dialog',name='Task audit');dialog.wait_for();assert dialog.get_by_text('1,234').is_visible() and dialog.get_by_text('2,501 units').is_visible();headers=dialog.locator('thead');assert headers.evaluate('(e)=>getComputedStyle(e).position')=='sticky';rows=dialog.locator('tbody tr');assert rows.count()==2;dialog.get_by_role('button',name='Actor').click();assert rows.first.locator('td').nth(1).inner_text()=='Shy';dialog.get_by_role('button',name='Actor').click();assert rows.first.locator('td').nth(1).inner_text()=='policy';dialog.get_by_label('Filter audit entries').fill('approve');assert rows.count()==1;assert rows.first.get_by_text('gate.approve').is_visible();close=dialog.get_by_role('button',name='Close task audit');vb=dialog.bounding_box();cb=close.bounding_box();assert cb['x']>=vb['x'] and cb['x']+cb['width']<=vb['x']+vb['width'];page.screenshot(path='/downloads/shared-audit-dialog.png',full_page=True);page.keyboard.press('Escape');assert dialog.count()==0

def test_model_route_default_and_phase_override_controls(page,ui):
    page.goto(ui+'/tasks/empty/view')
    picker=page.locator('#defaultroute');picker.wait_for()
    assert picker.locator('option').count()==2
    assert picker.input_value()=='deepseek'
    deepseek=picker.locator('option[value="deepseek"]')
    assert 'DeepSeek' in deepseek.inner_text()
    mock_gate_review(page,ui,'sim')
    page.goto(ui+'/tasks/sim/gates/gate-conops')
    page.locator('.model-panel summary').click()
    override=page.locator('select[name="next_model"]');override.wait_for()
    assert override.locator('option').first.inner_text()=='Use current task default'
    assert 'ollama:qwen2.5-coder:14b' in page.locator('[data-model-for=next_model]').inner_text()

def test_artifact_viewer_renders_content_title_bold_and_transcript(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='done';live['artifacts']=[{'phase_key':'intent','name':'intent_model.json','stale':False,'generation':1,'present':True,'valid':True,'validator':'IntentModel','validation_errors':[]}];live['ui_state']={'mode':'complete','active_phase':'tests','process_live':False,'decision':None,'gate':None}
    payload={'name':'intent_model.json','title':'Needs and Scope','phase':'intent','format':'markdown','display_name':'intent_model.md','content':'# What we understood\n\n**Problem:** Build habits\n\n## Interview questions and answers\n\n### Question 1\nWho needs this?\n\n**Answer:** Busy adults','raw_format':'json','raw_content':'{}'}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=_promote_control(live)));page.route('**/api/tasks/live/artifacts/intent/intent_model.json/view',lambda route:route.fulfill(json=payload));page.goto(ui+'/tasks/live/view');page.get_by_role('button',name='intent_model.json').click();viewer=page.locator('.artifact-viewer');viewer.wait_for()
    assert viewer.get_by_role('heading',name='Needs and Scope').is_visible()
    assert viewer.locator('strong').filter(has_text='Problem:').count()==1 and viewer.locator('strong').filter(has_text='Answer:').count()==1
    assert viewer.get_by_text('Who needs this?').is_visible() and viewer.get_by_text('Busy adults').is_visible()
    page.screenshot(path='/downloads/intent-viewer-transcript.png',full_page=True)


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


def test_interview_history_truncates_questions_and_answers_after_32_words(page, ui):
    route_task_snapshot(page, ui, "live", long_history_snapshot)
    page.goto(ui + "/tasks/live/view")
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


def test_feedback_thumbs_comment_persist_and_delete(page, ui):
    route_task_snapshot(page, ui, "live", interview_history_snapshot)
    page.goto(ui + "/tasks/live/view")
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


def test_feedback_save_disables_while_pending_and_closes_only_on_success(page, ui):
    route_task_snapshot(page, ui, "live", interview_history_snapshot)
    page.goto(ui + "/tasks/live/view")
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


def test_question_comment_columns_follow_character_ratio(page, ui):
    long_question = "What strategies will you implement to ensure data caching prevents downtime during peak usage?"
    def snapshot(data):
        interview_history_snapshot(data)
        data["events"][0]["message"] = long_question
        data["events"][0]["payload"]["question"] = long_question
    route_task_snapshot(page, ui, "live", snapshot)
    page.goto(ui + "/tasks/live/view")
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
    row.screenshot(path="/downloads/qa-column-balance-desktop-row.png")
    page.set_viewport_size({"width": 600, "height": 850})
    assert row.locator(".qa-main").bounding_box()["width"] > 220
    assert row.locator(".qa-notes").bounding_box()["y"] >= row.locator(".qa-main").bounding_box()["y"]
    row.screenshot(path="/downloads/qa-column-balance-mobile-row.png")
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
    page.screenshot(path='/downloads/protocol-error-state.png',full_page=True)

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
    page.screenshot(path='/downloads/failed-needs-retry.png',full_page=True)


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
    assert dialog.get_by_text('2,300').count()==1
    assert dialog.get_by_text('$0.0037 USD').count()==1
    page.screenshot(path='/downloads/audit-scroll-verified.png',full_page=True)


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
    page.screenshot(path='/downloads/needs-retry-model-selector.png',full_page=True)

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
    page.screenshot(path='/downloads/architecture-stale-issues-fixed.png',full_page=True)


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
    page.screenshot(path='/downloads/architecture-progress-normal.png',full_page=True)


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
    page.locator('#defaultroute').wait_for()
    page.evaluate("""() => {
      const original=window.fetch;
      window.fetch=(url,opts={})=>String(url).endsWith('/feedback')&&opts.method==='POST'
        ? new Promise(resolve=>setTimeout(()=>resolve(new Response('{}',{status:200,headers:{'content-type':'application/json'}})),1000))
        : original(url,opts);
      import('/assets/client-core.js').then(m=>m.api('/api/tasks/empty/feedback',{method:'POST',body:'{}'}));
    }""")
    page.locator('#server-wait:not(.hidden)').wait_for(timeout=3000)
    assert 'Saving feedback' in page.locator('#server-wait').inner_text()
    page.screenshot(path='/downloads/fast-follow-waiting-banner.png',full_page=False)
    page.locator('#server-wait').wait_for(state='hidden',timeout=3000)


def test_browser_session_refresh_single_flight_before_expiry(page,ui):
    import threading,time
    page.goto(ui+'/tasks/empty/view')
    page.locator('#defaultroute').wait_for()
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
    page.set_viewport_size({'width':1249,'height':900});page.screenshot(path='/downloads/decision-panel-closed.png')
    page.locator('.model-panel summary').click();page.locator('#gateform').screenshot(path='/downloads/decision-panel-model-open.png')
    page.screenshot(path='/downloads/specs-gate-ready.png',full_page=True)
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
    page.screenshot(path='/downloads/specs-gate-invalid.png',full_page=True)
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
    page.screenshot(path='/downloads/sticky-model-gate.png',full_page=True)

def test_final_package_surface_is_separate_from_debug_and_has_recoverable_link(page,ui):
 task=page.request.get(ui+'/api/tasks/done').json();page.route('**/api/tasks/done',lambda route:route.fulfill(json=_promote_control(task)))
 page.route('**/api/tasks/done/packages',lambda route:route.fulfill(json={'status':'ready','generation':1,'package_id':'a'*64,'url':'/api/tasks/done/packages/'+'a'*64+'?generation=1','contents':['README.md','interview/intent_model.json','spec/AGENTS.md']}))
 page.goto(ui+'/tasks/done/view');page.get_by_role('button',name='Prepare final package').click()
 link=page.get_by_role('link',name='Download coding-agent package');expect(link).to_be_visible();assert '?generation=1' in link.get_attribute('href')
 page.screenshot(path='/downloads/d8-final-package-desktop.png',full_page=True)
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/downloads/d8-final-package-mobile.png',full_page=True)
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
    page.screenshot(path='/downloads/shadow-copy.png')
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
    assert page.evaluate('document.documentElement.scrollHeight') < 1500  # 14 long exchanges must not stretch the page
    page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)'); page.wait_for_timeout(300)
    box = right.bounding_box()
    heading = page.get_by_role('heading', name='Live process'); hb = heading.bounding_box()
    assert 0 <= hb['y'] and hb['y'] + hb['height'] <= 800, hb  # Live process heading pinned in view after the page scrolled
    assert heading.evaluate('e=>{const r=e.getBoundingClientRect();const t=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e.contains(t)||t.contains(e)}')  # and not hidden behind the page header
    page.screenshot(path='/downloads/live-process-sticky.png')


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
    page.screenshot(path='/downloads/submitting-answer.png')
    page.get_by_text('Processing your answer', exact=False).wait_for()
    shadow = page.locator('#shadowcopy')
    assert shadow.evaluate('e=>getComputedStyle(e).animationName') == 'answer-pulse'
    assert shadow.evaluate('e=>parseFloat(getComputedStyle(e).borderTopWidth)') >= 3
    page.screenshot(path='/downloads/processing-answer.png')
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
    dialog = page.locator(".phase-change-dialog")
    dialog.wait_for(timeout=12000)
    text = dialog.inner_text()
    assert "Moving from Concept of operations to Stakeholder needs" in text
    assert "PHASE 3 OF 7" in text and "INPUT PAUSED" in text.upper()
    assert "Organizing your answers into stakeholder needs." in text
    assert "Your input is paused" in text
    page.evaluate("window.__emit({kind:'progress',phase_key:'needs',message:'Drafting need 2 of 6'})")
    assert "Latest: Drafting need 2 of 6" in page.locator("#transitionprogress").inner_text()
    # the page underneath keeps receiving the same progress signals
    assert "Drafting need 2 of 6" in page.locator("#stageprogress").inner_text()
    assert dialog.evaluate("e=>getComputedStyle(e).animationName") == "answer-pulse"
    page.screenshot(path="/downloads/phase-change-dialog.png")
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
    page.locator(".phase-change-dialog").wait_for(timeout=12000)
    cur["s"] = snap("needs", "engine_question")                        # input is possible again
    page.wait_for_function("!document.querySelector('.phase-change-dialog')", timeout=12000)
    # a phase change that arrives together with a question is not a paused state: no dialog
    cur["s"] = snap("srs", "engine_question")
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
    page.screenshot(path='/downloads/gate-flagged-checklist.png',full_page=True)
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
    page.screenshot(path='/downloads/failed-architecture-retry.png',full_page=True)
    dialog.get_by_role('button',name='Retry phase').click()
    page.wait_for_function('()=>true')
    assert sent[0]=={'phase':'architecture','confirm':False}
    assert sent[1]=={'phase':'architecture','confirm':True,'model_route':'deepseek','model':'deepseek-flash'}
