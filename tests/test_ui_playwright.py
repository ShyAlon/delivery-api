"""Browser-level contracts for the three task views and traceable rerun UX."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright


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
    page.goto(ui + "/tasks/sim/view")
    waiting=page.locator('.gate.waiting');waiting.wait_for()
    assert waiting.count()==1 and waiting.is_visible()
    assert page.locator('.gate.open').count()==1
    awaiting=page.locator('.phase.awaiting_approval');assert awaiting.count()==1
    palette=awaiting.locator('.dot').evaluate("e=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopColor})")
    assert palette['background']=='rgb(229, 182, 61)' and palette['border']=='rgb(229, 182, 61)'
    page.screenshot(path="/downloads/vmodel-gates-open-and-waiting.png", full_page=True)
    page.reload()
    page.locator('.gate.waiting').click()
    page.locator(".dialog").wait_for()
    assert "gate" in page.locator(".dialog h2").inner_text().lower()
    assert page.get_by_text("Waiting here · replay paused").is_visible()
    assert page.get_by_text("actor: local user", exact=False).is_visible()
    assert page.get_by_role("button", name="Redo").is_visible()
    page.screenshot(path="/downloads/vmodel-gate-modal.png", full_page=True)


def test_gate_redo_requires_steer_in_browser(page, ui):
    page.goto(ui + "/tasks/sim/view")
    page.locator('.gate.waiting').click()
    page.get_by_role("button", name="Redo").click()
    page.get_by_text("a redo needs a reason", exact=False).wait_for()
    assert page.get_by_text("a redo needs a reason", exact=False).is_visible()


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
    live['events']=[{'id':i,'kind':'progress','message':f'Output line {i}','phase_key':'intent','payload':{},'ts':1720000000+i} for i in range(120)]
    page.route('**/api/tasks/live',lambda route:route.fulfill(json=live))
    page.set_viewport_size({'width':1365,'height':800})
    page.goto(ui+'/tasks/live/view');console=page.locator('#console');console.wait_for()
    dimensions=console.evaluate('e=>({h:e.getBoundingClientRect().height,min:getComputedStyle(e).minHeight,max:getComputedStyle(e).maxHeight,scroll:e.scrollHeight>e.clientHeight})')
    assert dimensions=={'h':120,'min':'120px','max':'180px','scroll':True}
    composer=page.locator('#answerform');assert composer.is_visible()
    indicator=page.locator('.live-status-row .live-indicator');question=page.locator('#currentquestion');console_heading=page.locator('.console-heading')
    assert page.evaluate("([a,b,c,d])=>a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING&&b.compareDocumentPosition(c)&Node.DOCUMENT_POSITION_FOLLOWING&&c.compareDocumentPosition(d)&Node.DOCUMENT_POSITION_FOLLOWING",[indicator.element_handle(),question.element_handle(),composer.element_handle(),console_heading.element_handle()])
    assert console.bounding_box()['y']+console.bounding_box()['height'] <= 800
    indicator=page.locator('.live-indicator.working');assert indicator.get_by_text('Working · Start').is_visible()
    assert indicator.locator('i').evaluate("e=>getComputedStyle(e).animationName")=='live-halo'
    progress=page.locator('#progressfeed');assert progress.locator('.progress-item').count()==1
    first_stamp=progress.locator('.progress-item time').first.inner_text();assert '2024' in first_stamp and any(zone in first_stamp for zone in ('GMT','UTC','IDT','IST'))
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
    page.evaluate("liveSource.emit({kind:'waiting_input',message:'You:',phase_key:'intent',payload:{waiting_input:true,question:'Which users need this?\\nWhat outcome matters most?'},ts:1720000202})")
    assert page.locator('.live-indicator.waiting').get_by_text('Waiting for your answer').is_visible()
    assert question.is_visible() and question.locator('p').inner_text()=='Which users need this?\nWhat outcome matters most?'
    page.evaluate('''liveSource.emit({kind:"progress",message:"[17:02:08] ... still working - stage 'probing' call, 30s elapsed",phase_key:"intent",payload:{},ts:1720000203})''')
    assert question.is_visible() and question.locator('p').inner_text()=='Which users need this?\nWhat outcome matters most?'
    page.evaluate("liveSource.emit({kind:'waiting_input',message:'You:',phase_key:'conops',payload:{waiting_input:true,question:'Choose concept 1 or 2, then say why.'},ts:1720000203.5})")
    assert question.locator('p').inner_text()=='Choose concept 1 or 2, then say why.'
    assert composer.is_visible() and page.locator('.live-indicator.waiting').is_visible()
    page.evaluate("liveSource.emit({kind:'waiting_input',message:'You:',phase_key:'intent',payload:{waiting_input:true,question:'Second question?'},ts:1720000204})")
    assert question.locator('p').inner_text()=='Second question?'
    page.evaluate("liveSource.emit({kind:'waiting_input',message:'You:',phase_key:'intent',payload:{waiting_input:true,question:'Third question?'},ts:1720000205})")
    assert question.locator('p').inner_text()=='Third question?'
    parity=page.evaluate("([q,a])=>{let qs=getComputedStyle(q),as=getComputedStyle(a);return {qbg:qs.backgroundColor,abg:as.backgroundColor,qborder:qs.borderTopColor,aborder:as.borderTopColor,qr:qs.borderRadius,ar:as.borderRadius}}",[question.element_handle(),composer.element_handle()])
    assert parity['qbg']==parity['abg'] and parity['qborder']==parity['aborder'] and parity['qr']==parity['ar']
    page.route('**/api/tasks/live/answer',lambda route:route.fulfill(json={'status':'accepted'}))
    composer.locator('textarea').fill('Caregivers');composer.locator('button[type=submit]').click()
    assert question.is_visible() and question.locator('p').inner_text()=='Third question?'
    page.evaluate("liveSource.emit({kind:'answer',message:'You: Caregivers',phase_key:'intent',payload:{transcript:true},ts:1720000206})")
    assert console.locator('.line.answer',has_text='You: Caregivers').is_visible()

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
