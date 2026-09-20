"""Browser-level contracts for the three task views and traceable rerun UX."""
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
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        yield page
        browser.close()

def route_task_snapshot(page,ui,task_id,mutate):
    snapshot=page.request.get(f'{ui}/api/tasks/{task_id}').json();mutate(snapshot)
    page.route(f'**/api/tasks/{task_id}',lambda route:route.fulfill(json=snapshot))
    return snapshot

def waiting_gate_snapshot(snapshot):
    gate=snapshot['gates'][0];gate.update({'state':'waiting','is_open':0})
    for other in snapshot['gates'][1:]:other['state']='idle'
    snapshot.update({'state':'waiting_gate','active_phase':gate['after_phase']})
    for phase in snapshot['phases']:
        if phase['phase_key']==gate['after_phase']:phase.update({'state':'awaiting_approval','detail':'Generated; waiting for founder approval'})
    snapshot['ui_state']={'mode':'phase_gate','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':gate}


def test_dashboard_draws_v_and_all_task_states(page, ui):
    page.goto(ui)
    page.locator(".task").first.wait_for()
    assert page.locator(".task").count() == 4
    assert page.locator(".v-map svg").count() == 4
    assert page.get_by_text("Partial discovery evidence",exact=True).is_visible()
    partial=page.locator('.session-card',has_text='Partial discovery evidence')
    assert partial.locator('.session-v .status-done').count()==1
    assert partial.locator('.session-v .status-not_started').count()==7
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
    assert page.get_by_text('About Requi.re',exact=True).is_visible()
    assert page.locator('body').inner_text().lower().find('vector') == -1


def test_not_started_running_and_done_pages(page, ui):
    page.goto(ui + "/tasks/empty/view")
    page.get_by_text("This task hasn't started.").wait_for();assert page.get_by_text("This task hasn't started.").is_visible()
    start=page.get_by_role("button",name="Start interview");assert start.is_visible() and start.is_enabled()
    assert page.get_by_text("Start engine · wiring next").count()==0
    page.goto(ui + "/tasks/live/view")
    page.get_by_text("Live engine output").wait_for();assert page.get_by_text("Live engine output").is_visible()
    assert page.locator("#console").get_by_text("Generating CONOPS option 2/3").count() == 1
    assert page.locator("#progressfeed").get_by_text("Generating CONOPS option 2/3").count() == 1
    page.goto(ui + "/tasks/done/view")
    page.get_by_text("Artifacts", exact=True).wait_for();assert page.get_by_text("Artifacts", exact=True).is_visible()
    assert page.get_by_text("Architecture overview").is_visible()


def test_rerun_preview_explains_archive_and_audit_has_local_actor(page, ui):
    page.goto(ui + "/tasks/done/view")
    page.get_by_role("button", name="Rerun stage").click()
    assert page.get_by_text("Downstream artifacts will turn stale immediately", exact=False).is_visible()
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Audit").click()
    page.get_by_text("Task audit").wait_for()
    assert "local user" in page.locator(".dialog").inner_text()


def test_gate_open_waiting_halo_and_decision_modal(page, ui):
    route_task_snapshot(page,ui,'sim',waiting_gate_snapshot)
    page.goto(ui + "/tasks/sim/view")
    waiting=page.locator('#openphasegate');waiting.wait_for()
    assert waiting.count()==1 and waiting.is_visible()
    awaiting=page.locator('.phase.awaiting_approval');assert awaiting.count()==1
    palette=awaiting.locator('.dot').evaluate("e=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopColor})")
    assert palette['background']=='rgb(229, 182, 61)' and palette['border']=='rgb(229, 182, 61)'
    page.locator(".dialog").wait_for()
    assert page.get_by_text("Waiting here · replay paused").is_visible()
    assert page.get_by_text("actor: local user", exact=False).is_visible()
    assert page.get_by_role("button", name="Redo").is_visible()

def test_gate_redo_requires_steer_in_browser(page, ui):
    route_task_snapshot(page,ui,'sim',waiting_gate_snapshot)
    page.route('**/api/tasks/sim/gates/*/decide',lambda route:route.fulfill(status=409,json={'detail':{'error':'a redo needs a reason','cause':'Redo without a steer is a reroll.','next':'Describe what should change.'}}))
    page.goto(ui + "/tasks/sim/view");page.locator('#gateform').wait_for()
    page.get_by_role("button", name="Redo").click()
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
            page.get_by_text('shy@example.com',exact=True).wait_for();assert page.url==base+'/'
            assert page.locator('.avatar').inner_text()=='SH'
            page.reload();page.get_by_text('shy@example.com',exact=True).wait_for();assert page.locator('.avatar').is_visible()
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
            page.get_by_text("shy@example.com").wait_for()
            page.get_by_role("button",name="Sign out").click()
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
    page.get_by_role("link",name="Load sessions").wait_for();assert page.get_by_role("link",name="Load sessions").is_visible()
    assert page.get_by_role("button",name="Import run").count()==0
    page.get_by_role("link",name="Load sessions").click()
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

def test_live_output_fixed_scroll_pinning_composer_and_state(page,ui):
    # Own the stream in the browser so both pinned and deliberately unpinned
    # behavior are deterministic without timing the server's polling loop.
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json()
    live['active_phase']='intent'
    live['ui_state']={'mode':'engine_question','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    live['events']=[{'id':i,'kind':'progress','message':f'Output line {i}','phase_key':'intent','payload':{},'ts':1720000000+i} for i in range(120)]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.set_viewport_size({'width':1365,'height':800})
    page.goto(ui+'/tasks/live/view');console=page.locator('#console');console.wait_for()
    dimensions=console.evaluate('e=>({h:e.getBoundingClientRect().height,min:getComputedStyle(e).minHeight,max:getComputedStyle(e).maxHeight,scroll:e.scrollHeight>e.clientHeight})')
    assert dimensions['h']>=360 and dimensions['min']=='360px' and dimensions['max']=='640px' and dimensions['scroll'] is True
    composer=page.locator('#answerform');assert composer.is_visible()
    indicator=page.locator('.live-status-row .live-indicator');question=page.locator('#currentquestion');console_heading=page.locator('.console-heading')
    assert page.evaluate("([a,b,c,d])=>a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING&&b.compareDocumentPosition(c)&Node.DOCUMENT_POSITION_FOLLOWING&&c.compareDocumentPosition(d)&Node.DOCUMENT_POSITION_FOLLOWING",[indicator.element_handle(),question.element_handle(),composer.element_handle(),console_heading.element_handle()])
    assert console.bounding_box()['height']>=180
    indicator=page.locator('.live-indicator.waiting');assert indicator.get_by_text('Waiting for your answer').is_visible()
    progress=page.locator('#progressfeed');assert progress.locator('.progress-item').count()==1
    assert progress.locator('.progress-item time').count()==0 and console.locator('.line time').count()==0
    page.screenshot(path='/downloads/requi-re-live-output.png',full_page=True)

    console.evaluate('e=>e.scrollTop=60');page.wait_for_timeout(50);before=console.evaluate('e=>e.scrollTop')
    page.evaluate("liveSource.emit({kind:'progress',message:'new while reading',phase_key:'conops',payload:{},ts:1720000200})")
    assert console.evaluate('e=>e.scrollTop')==before
    console.evaluate('e=>e.scrollTop=e.scrollHeight');page.wait_for_timeout(50)
    page.evaluate("liveSource.emit({kind:'progress',message:'new while pinned',phase_key:'conops',payload:{},ts:1720000201})")
    assert console.evaluate('e=>Math.abs(e.scrollHeight-e.scrollTop-e.clientHeight)<2')
    assert progress.locator('.progress-item',has_text='new while pinned').is_visible()
    assert progress.locator('.progress-item').count()==1
    page.evaluate('''liveSource.emit({kind:"progress",message:"[17:01:25] ... still working - stage 'conops' call, 30s elapsed",phase_key:"conops",payload:{},ts:1720000201.5})''')
    inflight=progress.locator('.progress-item');assert inflight.get_by_text("stage 'conops' call",exact=False).is_visible()
    assert inflight.get_by_text('30s elapsed',exact=False).is_visible() and inflight.locator('small').inner_text()=='Conops'
    page.evaluate("liveSource.emit({kind:'progress',message:'[17:01:38] ok Planning CONOPS concept option 1/2 [42.6s]',phase_key:'conops',payload:{},ts:1720000201.8})")
    assert progress.locator('.progress-item',has_text='ok Planning CONOPS concept option 1/2').is_visible()
    assert not progress.get_by_text("stage 'conops' call",exact=False).is_visible()

def test_answer_failure_preserves_text_and_recovers_button(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.route('**/api/tasks/live/answer',lambda route:route.fulfill(status=503,json={'detail':{'error':'Answer could not be sent.','cause':'Engine input is unavailable.','next':'Retry when the engine is waiting.'}}))
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea');button=page.locator('#answerform button[type=submit]')
    answer.fill('Do not lose this answer');button.click()
    page.get_by_text('Answer could not be sent.',exact=False).wait_for()
    assert answer.input_value()=='Do not lose this answer' and button.is_enabled() and button.inner_text()=='Send answer'

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
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    pending=[]
    def hold(route):pending.append(route)
    page.route('**/api/tasks/live/answer',hold)
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea');send=page.locator('#answerform button[type=submit]');cancel=page.get_by_role('button',name='Cancel send')
    answer.fill('A careful answer still in flight');send.click();cancel.wait_for()
    page.wait_for_timeout(16000)
    assert send.inner_text()=='Sending…' and send.is_disabled() and cancel.is_visible()
    assert answer.input_value()=='A careful answer still in flight' and page.get_by_text('request timed out',exact=False).count()==0
    cancel.click();page.get_by_text('Send cancelled. Your answer is still here.').wait_for()
    assert send.is_enabled() and answer.input_value()=='A careful answer still in flight'
    if pending:pending[0].abort()

def test_answer_enter_sends_and_control_enter_inserts_newline(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent'
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    sent=[]
    page.route('**/api/tasks/live/answer',lambda route:(sent.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');answer=page.locator('#answerform textarea')
    answer.fill('Line one');answer.press('Control+Enter');answer.type('Line two')
    assert answer.input_value()=='Line one\nLine two' and sent==[]
    answer.press('Enter');page.get_by_text('Answer sent.',exact=False).wait_for()
    assert sent==[{'answer':'Line one\nLine two'}]

def test_structured_founder_decision_opens_large_card_dialog_and_formats_answer(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[];live['active_phase']='conops'
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    submitted=[]
    page.route('**/api/tasks/live/answer',lambda route:(submitted.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    decision={'type':'founder_decision','decision_id':'concept','title':'Choose the operating concept','prompt':'Which concept should the architecture execute?','rationale_required':True,'rationale_label':'Why is this right?','answer_format':'<value> - <rationale>','options':[{'value':'1','label':'Native app','summary':'Installed experience','advantages':['Fast offline'],'disadvantages':['App stores']},{'value':'2','label':'PWA','summary':'Browser first','advantages':['One deploy'],'disadvantages':['Limited APIs']}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':decision}
    live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"conops",payload:{waiting_input:true,question:decision.prompt,decision},ts:1720000300})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.is_visible()
    assert dialog.get_by_text('Native app',exact=True).is_visible() and dialog.get_by_text('PWA',exact=True).is_visible()
    assert dialog.get_by_text('Fast offline',exact=False).is_visible() and dialog.get_by_text('Limited APIs',exact=False).is_visible()
    box=dialog.bounding_box();assert box['width']>900
    dialog.get_by_text('PWA',exact=True).click();dialog.locator('textarea').fill('One deployment fits our team')
    page.screenshot(path='/downloads/structured-founder-decision.png',full_page=True)
    dialog.locator('textarea').press('Enter')
    assert submitted==[{'answer':'2 - One deployment fits our team','decision_id':'concept'}]


def test_structured_founder_decision_escape_closes_without_answer(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    decision={'type':'founder_decision','title':'Choose','prompt':'Pick','options':[{'value':'1','label':'A','summary':'First'},{'value':'2','label':'B','summary':'Second'}]}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:"Pick",phase_key:"conops",payload:{waiting_input:true,decision},ts:1720000300})',decision)
    page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape');assert page.locator('.decision-dialog').count()==0
    assert page.locator('#opendecision').is_visible()

def test_stored_pending_decision_reopens_on_page_load_and_panels_align(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='conops'
    decision={'type':'founder_decision','decision_id':'concept','title':'Choose after restart','prompt':'Which concept?','options':[{'value':'1','label':'Native','summary':'Installed'},{'value':'2','label':'PWA','summary':'Browser'}]}
    live['events']=[{'id':900,'kind':'founder_decision','message':'Which concept?','phase_key':'conops','payload':{'waiting_input':True,'question':'Which concept?','decision':decision},'ts':1720000300},{'id':901,'kind':'waiting_input','message':'You:','phase_key':'conops','payload':{'waiting_input':True,'question':'Which concept?'},'ts':1720000301}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.set_viewport_size({'width':1365,'height':900});page.goto(ui+'/tasks/live/view')
    page.locator('.decision-dialog').wait_for();assert page.get_by_role('heading',name='Choose after restart').is_visible()
    page.keyboard.press('Escape');assert page.locator('#opendecision').is_visible()
    panels=page.locator('.detail-layout>.panel, .detail-layout>div>.panel');assert panels.count()==2
    boxes=[panels.nth(i).bounding_box() for i in range(2)]
    assert abs((boxes[0]['y']+boxes[0]['height'])-(boxes[1]['y']+boxes[1]['height']))<2
    console=page.locator('.live-console').bounding_box();assert console['height']>=180
    page.screenshot(path='/downloads/restart-decision-aligned-panels.png',full_page=True)

def test_detail_layout_never_crushes_or_overlaps_at_user_viewport(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='conops';long='Reply with the option number or exact concept name, then ` - ` and why you chose it. This is an explicit founder decision.'
    decision={'type':'founder_decision','title':'Choose the operating concept','prompt':long,'options':[{'value':'1','label':'Native','summary':'Installed'},{'value':'2','label':'PWA','summary':'Browser'}]}
    live['events']=[{'id':1,'kind':'founder_decision','message':long,'phase_key':'conops','payload':{'waiting_input':True,'question':long,'decision':decision},'ts':1720000300}]
    # Doctrine colors: approved green, generated yellow, active blue, pending gray.
    states=['approved','awaiting_approval','running','pending','pending','pending','pending']
    for phase,state in zip(live['phases'],states):phase['state']=state;phase['detail']='Waiting for founder approval' if state=='awaiting_approval' else ''
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.set_viewport_size({'width':1390,'height':827});page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape')
    left=page.locator('.detail-layout>.panel');right=page.locator('.detail-layout>div>.panel')
    lb,rb=left.bounding_box(),right.bounding_box();assert abs((lb['y']+lb['height'])-(rb['y']+rb['height']))<2
    indicator=page.locator('.live-indicator');assert indicator.evaluate('e=>e.scrollWidth<=e.clientWidth')
    question=page.locator('#currentquestion p');assert question.evaluate('e=>e.scrollHeight<=e.clientHeight')
    progress=page.locator('.live-progress');assert progress.bounding_box()['x']>=right.bounding_box()['x']
    palette={s:page.locator(f'.phase.{s} .dot').first.evaluate('e=>getComputedStyle(e).backgroundColor') for s in states}
    assert palette['approved']=='rgb(22, 163, 106)' and palette['awaiting_approval']=='rgb(229, 182, 61)' and palette['running']=='rgb(78, 156, 247)' and palette['pending']=='rgb(242, 240, 233)'
    assert page.locator('.phase.running').count()==1
    console=page.locator('.live-console');assert console.evaluate('e=>getComputedStyle(e).wordBreak')=='normal'
    page.screenshot(path='/downloads/detail-layout-clean-1390x827.png',full_page=True)

def test_founder_dialog_fixed_controls_and_vertical_four_options(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['events']=[]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.route('**/api/tasks/live/answer',lambda route:route.fulfill(json={'status':'accepted'}))
    page.set_viewport_size({'width':1390,'height':827});page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    options=[{'value':str(i),'label':name,'summary':'A deliberately detailed option summary.','advantages':['Clear advantage'],'disadvantages':['Known tradeoff']} for i,name in enumerate(['Native mobile','Progressive web app','Responsive web','Hybrid shell'],1)]
    decision={'type':'founder_decision','title':'Choose the operating concept','prompt':'Which concept should the architecture execute?','rationale_label':'Why is this the right concept?','options':options}
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
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    decision={'type':'founder_decision','title':'Choose one','prompt':'Choose to continue','options':[{'value':'1','label':'A','summary':'First'},{'value':'2','label':'B','summary':'Second'}]}
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"conops",payload:{waiting_input:true,decision},ts:1720000300})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();button=dialog.locator('.decision-submit')
    assert float(button.evaluate("e=>getComputedStyle(e).borderRadius.replace('px','')"))>=12
    page.locator('#modal').click(position={'x':5,'y':5});assert dialog.count()==0
    launch=page.locator('#opendecision');assert launch.is_visible()
    assert page.locator('.live-indicator.waiting').get_by_text('Waiting for your answer').is_visible()
    launch.click();assert page.locator('.decision-dialog').is_visible()

def test_decision_ready_bar_is_first_and_sticky_during_page_scroll(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','title':'Choose the operating concept','prompt':'Choose','options':[{'value':'1','label':'A'},{'value':'2','label':'B'}]}
    live['events']=[{'id':1,'kind':'founder_decision','message':'Choose','phase_key':'conops','payload':{'waiting_input':True,'question':'Choose','decision':decision},'ts':1720000300}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.set_viewport_size({'width':1390,'height':620});page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for();page.keyboard.press('Escape')
    launch=page.locator('#opendecision');panel=page.locator('.detail-layout>div>.panel');assert launch.is_visible()
    assert panel.evaluate("e=>e.firstElementChild.nextElementSibling?.id==='opendecision'")
    assert launch.evaluate("e=>getComputedStyle(e).position")=='sticky'
    page.evaluate('window.scrollTo(0,document.body.scrollHeight)');page.wait_for_timeout(100)
    box=launch.bounding_box();assert box and box['y']>=80 and box['y']<160
    assert launch.get_by_text('Required to continue',exact=False).is_visible()
    launch.click();assert page.locator('.decision-dialog').is_visible()
    page.screenshot(path='/downloads/decision-ready-sticky.png',full_page=False)

def test_answered_decision_clears_and_binary_approval_uses_same_dialog(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();concept={'type':'founder_decision','title':'Old concept decision','prompt':'Choose','options':[{'value':'1','label':'A'},{'value':'2','label':'B'}]}
    live['events']=[{'id':10,'kind':'founder_decision','message':'Choose','phase_key':'conops','payload':{'waiting_input':True,'decision':concept},'ts':1720000000},{'id':11,'kind':'answer','message':'You: 1 - reason','phase_key':'conops','payload':{'transcript':True},'ts':1720000001}]
    submitted=[];page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.route('**/api/tasks/live/answer',lambda route:(submitted.append(route.request.post_data_json),route.fulfill(json={'status':'accepted'})))
    page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for();assert page.locator('#opendecision').count()==0 and page.locator('.decision-dialog').count()==0
    approval={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'answer_format':'<value>','options':[{'value':'y','label':'Approve','summary':'Continue'},{'value':'n','label':'Reject','summary':'Stop'}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':approval};live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':approval,'gate':None}
    page.unroute('**/api/tasks/live');page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"intent",payload:{waiting_input:true,decision},ts:1720000300})',approval)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.get_by_text('Approve',exact=True).is_visible() and dialog.get_by_text('Reject',exact=True).is_visible();assert dialog.locator('.decision-why').is_hidden()
    page.screenshot(path='/downloads/typed-intent-approval.png',full_page=True)
    assert dialog.locator('.decision-submit').is_hidden();dialog.get_by_text('Approve',exact=True).click();page.locator('.decision-dialog').wait_for(state='detached');assert submitted==[{'answer':'y','decision_id':'intent-approval'}]
    assert page.locator('#opendecision').count()==0 and page.locator('.live-indicator').get_by_text('Decision received',exact=False).is_visible()


def test_stale_structured_decision_is_not_actionable_on_load(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'old-intent','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[{'id':91,'kind':'founder_decision','message':'Approve?','phase_key':'intent','payload':{'waiting_input':True,'decision':decision},'ts':1720000300}]
    live['input_state']={'waiting':False,'process_live':False,'decision':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    assert page.locator('#opendecision').count()==0 and page.locator('.decision-dialog').count()==0


def test_stale_decision_conflict_closes_and_refreshes_dialog(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[];live['input_state']={'waiting':True,'process_live':True,'decision':decision};calls={'get':0}
    def task(route):
        calls['get']+=1
        if calls['get']>1:live['input_state']={'waiting':False,'process_live':False,'decision':None}
        route.fulfill(json=live)
    page.route('**/api/tasks/live',task)
    page.route('**/api/tasks/live/answer',lambda route:route.fulfill(status=409,json={'detail':{'error':'Stale founder decision was not submitted.','cause':'The submitted decision is no longer open.','next':'Refresh shows the current state.','stale':True,'current_state':'running','active_phase':'needs'}}))
    page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for();dialog.get_by_text('Reject',exact=True).click()
    page.get_by_text('Stale founder decision was not submitted.',exact=False).wait_for();page.wait_for_timeout(900)
    assert page.locator('.decision-dialog').count()==0 and page.locator('#opendecision').count()==0 and calls['get']>=2

def test_intent_approval_shows_selected_concept_and_founder_reason(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'context':{'label':'Selected concept','value':'PWA','reason':'You stated you chose this concept because one deployment fits our team'},'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['events']=[{'id':91,'kind':'founder_decision','message':'Approve?','phase_key':'intent','payload':{'waiting_input':True,'decision':decision},'ts':1720000300}];live['input_state']={'waiting':True,'process_live':True,'decision':decision}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view')
    context=page.locator('.decision-context');context.wait_for();assert context.get_by_text('PWA',exact=True).is_visible()
    assert context.get_by_text('You stated you chose this concept because one deployment fits our team',exact=True).is_visible()
    page.screenshot(path='/downloads/intent-approval-with-concept-reason.png',full_page=True)

def test_browser_logs_runtime_version_and_reloads_when_server_changes(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    console=[];page.on('console',lambda msg:console.append(msg.text))
    page.goto(ui+'/');page.wait_for_timeout(300)
    assert any('runtime version' in line and 'development' in line for line in console)
    # The watcher contract compares the server runtime and reloads on mismatch.
    source=(Path(__file__).parents[1]/'src/delivery_api/web/app.js').read_text()
    assert "live.runtime_id!==runtimeVersion" in source and 'location.reload()' in source

def test_detail_uses_one_large_v_process_without_duplicate_path_map(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    page.goto(ui+'/tasks/live/view');phases=page.locator('.phase-list-primary .phase');phases.first.wait_for()
    assert phases.count()==7 and page.locator('.detail-layout .v-map').count()==0 and page.locator('.detail-layout .gate-legend').count()==0
    first=phases.first.bounding_box();assert first['height']>=70

def test_server_accepted_decision_event_closes_founder_box(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    live['input_state']={'waiting':True,'process_live':True,'decision':decision};live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':decision,'gate':None,'show_start':False,'show_progress':True,'show_engine_decision':True,'show_question':False,'show_answer_composer':False,'show_phase_gate':False,'show_artifacts':False,'show_console':True};page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.goto(ui+'/tasks/live/view');page.locator('.decision-dialog').wait_for()
    page.evaluate("liveSource.emit({kind:'answer',message:'You: y',phase_key:'intent',payload:{decision_id:'intent-approval',answer:'y'},ts:1720000400})")
    page.locator('.decision-dialog').wait_for(state='detached');assert page.locator('#opendecision').count()==0

def test_needs_gate_state_hides_stale_intent_prompt_and_opens_needs_gate(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();gate=next(g for g in live['gates'] if g['after_phase']=='needs');gate['state']='waiting'
    live.update({'state':'waiting_gate','active_phase':'needs','input_state':{'waiting':False,'process_live':False,'decision':None},
                 'ui_state':{'mode':'phase_gate','active_phase':'needs','process_live':False,'decision':None,'gate':gate,'show_start':False,'show_progress':False,'show_engine_decision':False,'show_question':False,'show_answer_composer':False,'show_phase_gate':True,'show_artifacts':False,'show_console':True}})
    live['events']=[{'id':1,'kind':'waiting_input','message':'Approve this intent model? [y/N]','phase_key':'intent','payload':{'waiting_input':True,'question':'Approve this intent model? [y/N]'},'ts':1720000000}]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view')
    page.locator('#gateform').wait_for();assert page.locator('.current-question').is_hidden() and page.locator('#answerform').is_hidden()
    assert page.locator('.decision-dialog').count()==0 and page.locator('#opendecision').count()==0
    assert page.locator('#gateform').get_by_text('Stakeholder needs gate',exact=True).is_visible()


def test_managed_task_state_reducer_is_exhaustive_and_atomic(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){this.closed=true}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json()
    decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve the intent model','prompt':'Approve this intent model?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    calls={'n':0}
    def route(request):
        calls['n']+=1
        if calls['n']>1:
            live['input_state']={'waiting':True,'process_live':True,'decision':decision}
            live['ui_state']={'mode':'engine_decision','active_phase':'intent','process_live':True,'decision':decision,'gate':None}
        request.fulfill(json=live)
    page.route('**/api/tasks/live',route);page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for()
    page.evaluate('(decision)=>liveSource.emit({kind:"founder_decision",message:decision.prompt,phase_key:"intent",payload:{waiting_input:true,decision},ts:1720000500})',decision)
    dialog=page.locator('.decision-dialog');dialog.wait_for();assert dialog.get_by_text('Approve',exact=True).is_visible()
    assert page.locator('#answerform').is_hidden() and page.locator('#opendecision').is_visible()
    page.screenshot(path='/downloads/managed-state-intent-decision.png',full_page=True)
    app_source=(Path(__file__).parents[1]/'src/delivery_api/web/app.js').read_text();state_source=(Path(__file__).parents[1]/'src/delivery_api/web/ui-state.js').read_text()
    assert 'reduceTaskSnapshot' in app_source and 'next.mode!==view.mode' in app_source and 'TASK_UI_ACTIONS' in state_source


def test_runtime_build_identity_is_visible(page,ui):
    page.goto(ui+'/');page.locator('#accountbutton').click();badge=page.locator('#identity .runtime-id');badge.wait_for();assert badge.inner_text()=='BUILD develop'


def test_sse_snapshot_refresh_is_filtered_and_coalesced(page,ui):
    page.add_init_script("""class FakeEventSource{constructor(){window.liveSource=this;this.handlers={}}addEventListener(k,f){this.handlers[k]=f}close(){this.closed=true}emit(e){this.handlers.progress({data:JSON.stringify(e)})}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();calls={'n':0}
    def route(request):calls.__setitem__('n',calls['n']+1);request.fulfill(json=live)
    page.route('**/api/tasks/live',route);page.goto(ui+'/tasks/live/view');page.locator('#console').wait_for();baseline=calls['n']
    page.evaluate("""for(let i=0;i<100;i++)liveSource.emit({kind:'progress',message:'line '+i,phase_key:'intent',payload:{},ts:1720001000+i})""")
    page.wait_for_timeout(200);assert calls['n']==baseline
    page.evaluate("""for(let i=0;i<50;i++)liveSource.emit({kind:'founder_decision',message:'decision '+i,phase_key:'intent',payload:{waiting_input:true},ts:1720002000+i})""")
    page.wait_for_timeout(250);assert calls['n']==baseline+1
    source=(Path(__file__).parents[1]/'src/delivery_api/web/app.js').read_text()
    assert "stateEvents=new Set" in source and 'snapshotPending' in source and 'setTimeout(snapshotRefresh,75)' in source


def test_runtime_badge_survives_identity_render(page,ui):
    page.route('**/health',lambda route:route.fulfill(json={'ok':True,'runtime_id':'7d6647f123456','repositories':{}}))
    page.route('**/api/auth',lambda route:(page.wait_for_timeout(150),route.fulfill(json={'mode':'basic','authenticated':True,'actor':'shy@example.com','admin':True,'providers':[]})))
    page.goto(ui+'/');page.locator('#accountbutton').click();badge=page.locator('#identity .runtime-id');badge.wait_for();assert badge.inner_text()=='BUILD 7d6647f'
    page.locator('#identity').get_by_text('shy@example.com',exact=True).wait_for();assert page.locator('#identity .runtime-id').inner_text()=='BUILD 7d6647f'


def test_orphaned_running_task_renders_visible_recovery_error(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='intent';live['input_state']={'waiting':False,'process_live':False,'decision':None};live['ui_state']={'mode':'error','active_phase':'intent','process_live':False,'decision':None,'gate':None};live['events'].append({'id':999,'kind':'error','message':'Error: engine stream handling stopped. Likely cause: protocol failure. Next: restart Requi.re.','phase_key':'intent','payload':{},'ts':1720003000})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view')
    notice=page.locator('.task-error-state');notice.wait_for();assert notice.get_by_text('Engine is not running.',exact=True).is_visible()
    assert page.locator('.live-indicator').get_by_text('Engine stopped',exact=True).is_visible()

def test_gate_checkbox_updates_in_place_without_navigation(page,ui):
    live=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in live['gates'] if g['state']=='waiting');live['ui_state']={'mode':'phase_gate','active_phase':live['active_phase'],'process_live':False,'decision':None,'gate':gate};calls=[]
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=live))
    page.route('**/api/tasks/sim/gates/*/toggle',lambda route:(calls.append(route.request.post_data_json),route.fulfill(json={**gate,'is_open':route.request.post_data_json['is_open']})))
    navigations=[];page.on('framenavigated',lambda frame:navigations.append(frame.url) if frame==page.main_frame else None)
    page.goto(ui+'/tasks/sim/view');page.locator('#gateform').wait_for();before=len(navigations);box=page.locator('#gateopen');box.check()
    page.get_by_text('Gate opened.',exact=True).wait_for();assert box.is_checked() and calls==[{'is_open':True}]
    assert len(navigations)==before and page.locator('#gateform').is_visible()

def test_gate_dialog_explains_controls_and_tooltips_desktop_and_phone(page,ui):
    live=page.request.get(ui+'/api/tasks/sim').json();gate=next(g for g in live['gates'] if g['state']=='waiting');live['ui_state']={'mode':'phase_gate','active_phase':live['active_phase'],'process_live':False,'decision':None,'gate':gate}
    page.route('**/api/tasks/sim',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/sim/view');dialog=page.locator('#gateform');dialog.wait_for()
    for text in ['This checkpoint decides','Approve the selected option','Approve your rewritten decision','Regenerate from this checkpoint']:
        assert dialog.get_by_text(text,exact=False).is_visible()
    help_button=dialog.get_by_role('button',name='What does opening a gate do?');help_button.focus();tip=dialog.get_by_role('tooltip').filter(has_text='auto-approves this checkpoint');expect(tip).to_be_visible()
    page.screenshot(path='/downloads/gate-help-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.goto(ui+'/tasks/sim/view');dialog=page.locator('#gateform');dialog.wait_for();dialog.get_by_role('button',name='Regenerate this phase').focus()
    tip=dialog.get_by_role('tooltip').filter(has_text='keeps earlier generations');expect(tip).to_be_visible();box=tip.bounding_box();assert box['x']>=0 and box['x']+box['width']<=390
    page.screenshot(path='/downloads/gate-help-phone.png',full_page=True)


def test_decision_dialog_rationale_help_is_real_and_keyboard_accessible(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();decision={'type':'founder_decision','decision_id':'concept','title':'Choose one','prompt':'Which concept?','rationale_required':True,'options':[{'value':'1','label':'Native'},{'value':'2','label':'PWA'}]};live['ui_state']={'mode':'engine_decision','active_phase':'conops','process_live':True,'decision':decision,'gate':None}
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');dialog=page.locator('.decision-dialog');dialog.wait_for();button=dialog.get_by_role('button',name='Why a rationale is requested');button.focus();expect(dialog.get_by_role('tooltip')).to_be_visible()
    expect(dialog.get_by_role('tooltip').get_by_text('stored with the selected option',exact=False)).to_be_visible()

def test_live_log_is_prominent_without_duplicate_timestamps_desktop_and_phone(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['active_phase']='intent';live['ui_state']={'mode':'executing','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    live['events']=[{'id':i,'kind':'progress','message':f'[17:01:{i%60:02d}] Building requirement {i}','phase_key':'intent','payload':{},'ts':1720000000+i} for i in range(80)]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.set_viewport_size({'width':1365,'height':800});page.goto(ui+'/tasks/live/view');console=page.locator('#console');console.wait_for()
    assert page.get_by_text('REAL PROGRESS · ALL STAGES',exact=True).count()==0
    assert page.locator('#progressfeed time').count()==0 and console.locator('.line time').count()==0
    dimensions=console.evaluate('e=>({height:e.getBoundingClientRect().height,min:getComputedStyle(e).minHeight,max:getComputedStyle(e).maxHeight,scroll:e.scrollHeight>e.clientHeight})')
    assert dimensions['height']>=360 and dimensions['min']=='360px' and dimensions['max']=='640px' and dimensions['scroll'] is True
    page.screenshot(path='/downloads/live-log-prominent-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.reload();console=page.locator('#console');console.wait_for()
    assert console.bounding_box()['height']>=360 and page.get_by_text('REAL PROGRESS · ALL STAGES',exact=True).count()==0
    page.screenshot(path='/downloads/live-log-prominent-phone.png',full_page=True)

def test_mutation_flows_refresh_in_place_without_document_navigation(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}close(){}}window.EventSource=FakeEventSource""")
    base=page.request.get(ui+'/api/tasks/live').json();base.update({'task_id':'mutation-test','title':'Mutation test','state':'not_started','active_phase':None,'ui_state':{'mode':'not_started','active_phase':None,'process_live':False,'decision':None,'gate':None}});renamed={**base,'title':'Renamed in place'};reads=0
    def mutation_task(route):
        nonlocal reads
        if route.request.method=='PATCH':route.fulfill(json={'status':'renamed'})
        else:reads+=1;route.fulfill(json=base if reads==1 else renamed)
    page.route('**/api/tasks/mutation-test',mutation_task);page.goto(ui+'/tasks/mutation-test/view');page.get_by_role('button',name='Rename').click();page.locator('#renameform input').fill('Renamed in place')
    nav=[];page.on('framenavigated',lambda frame:nav.append(frame.url) if frame==page.main_frame else None);before=len(nav)
    page.locator('#renameform').get_by_role('button',name='Save name').click();page.get_by_role('heading',name='Renamed in place').wait_for();assert len(nav)==before

    live=page.request.get(ui+'/api/tasks/sim').json();gate=live['gates'][0];gate.update({'state':'waiting','is_open':0});live.update({'state':'waiting_gate','active_phase':gate['after_phase'],'ui_state':{'mode':'phase_gate','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':gate}})
    decided={**live,'state':'running','ui_state':{'mode':'error','active_phase':gate['after_phase'],'process_live':False,'decision':None,'gate':None}};calls=0
    def task(route):
        nonlocal calls;calls+=1;route.fulfill(json=live if calls==1 else decided)
    page.route('**/api/tasks/sim',task);page.route('**/api/tasks/sim/gates/*/decide',lambda route:route.fulfill(json={**gate,'state':'decided'}))
    page.goto(ui+'/tasks/sim/view');page.locator('#gateform').wait_for();before=len(nav);page.get_by_role('button',name='Choose & continue').click();page.get_by_text('Engine is not running.',exact=True).wait_for();assert len(nav)==before

def test_live_progress_scope_and_account_menu_desktop_and_phone(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();phase=next(p for p in live['phases'] if p['phase_key']==live['active_phase']);phase.update({'completed':8,'total':20,'started_at':time.time()-12*60})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.route('**/health',lambda route:route.fulfill(json={'ok':True,'runtime_id':'abcdef123456','repositories':{}}))
    page.route('**/api/auth',lambda route:route.fulfill(json={'mode':'basic','authenticated':True,'actor':'shy@example.com','admin':True,'providers':[]}))
    page.set_viewport_size({'width':1365,'height':800});page.goto(ui+'/tasks/live/view')
    scope=page.locator('.progress-scope');scope.wait_for();assert scope.locator('b').all_inner_texts()==['8','20','12'];assert 'out of' in scope.inner_text() and 'minutes so far' in scope.inner_text()
    page.locator('#accountbutton').click();menu=page.locator('#accountmenu');menu.wait_for();assert menu.get_by_text('shy@example.com',exact=True).is_visible();assert menu.get_by_text('BUILD abcdef1',exact=True).is_visible();assert menu.get_by_role('menuitem',name='Sign out').is_visible()
    page.screenshot(path='/downloads/live-scope-account-desktop.png',full_page=True);page.keyboard.press('Escape');assert menu.is_hidden()
    page.set_viewport_size({'width':390,'height':844});page.reload();scope.wait_for();page.locator('#accountbutton').click();menu.wait_for();page.screenshot(path='/downloads/live-scope-account-phone.png',full_page=True)

def test_recovered_run_hides_stale_sigterm_error_from_live_console(page,ui):
    page.add_init_script("""class FakeEventSource{addEventListener(){}}window.EventSource=FakeEventSource""")
    live=page.request.get(ui+'/api/tasks/live').json();live['state']='running';live['active_phase']='intent';live['ui_state']={'mode':'executing','active_phase':'intent','process_live':True,'decision':None,'gate':None}
    live['events']=[
      {'id':1,'kind':'error','message':'Error: interview engine stopped with exit code -15.','phase_key':'intent','payload':{'exit_code':-15},'ts':1720000000},
      {'id':2,'kind':'command','message':'Engine process started','phase_key':'intent','payload':{},'ts':1720000100},
      {'id':3,'kind':'progress','message':'Recovered checkpoint is running','phase_key':'intent','payload':{},'ts':1720000101},
    ]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');console=page.locator('#console');console.wait_for()
    assert 'Recovered checkpoint is running' in console.inner_text();assert 'exit code -15' not in console.inner_text();assert page.locator('.task-error-state').count()==0

def test_live_elapsed_uses_latest_phase_command_not_parked_phase_start(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json();phase=next(p for p in live['phases'] if p['phase_key']==live['active_phase']);phase.update({'started_at':time.time()-22*3600,'completed':None,'total':None})
    live['events'].append({'id':999,'kind':'command','message':'Engine process started','phase_key':live['active_phase'],'payload':{},'ts':time.time()-12*60})
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live));page.goto(ui+'/tasks/live/view');scope=page.locator('.progress-scope');scope.wait_for()
    assert '12 minutes so far' in scope.inner_text();assert '22 hours' not in scope.inner_text()

def test_avatar_menu_does_not_emit_native_runtime_tooltip(page,ui):
    page.goto(ui+'/');button=page.locator('#accountbutton');button.wait_for();assert page.locator('#identity').get_attribute('title') is None
