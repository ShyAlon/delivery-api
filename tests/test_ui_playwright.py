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
    assert page.get_by_text("Generating CONOPS option 2/3").count() == 1
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
    page.get_by_text("Intent model").wait_for();assert page.get_by_text("Intent model").is_visible()
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
    assert not page.get_by_text("Intent model").is_visible()
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
