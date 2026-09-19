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
    (session/"interview/audit.jsonl").write_text(json.dumps({"event":"gate.auto_decision","gate":"concept","action":"select"})+"\n")
    (session/"spec/hands-free-run.json").write_text(json.dumps({"completed_at":"2026-09-19T08:00:00Z"}))
    (session/"spec/bundle-integrity.json").write_text(json.dumps({"files_checked":14,"diagnostics":[],"hashes":{"needs.json":"abc"}}))
    (session/"spec/needs.json").write_text(json.dumps({"needs":[{"need_id":"N-1","statement":"Families need visible task status"}]}))
    (session/"spec/srs.json").write_text(json.dumps({"functional":[{"requirement_id":"R-1","title":"Visible status","statement":"The system shall show status","need_ids":["N-1"],"acceptance":[{"criterion_id":"AC-1","statement":"Status is visible"}]}]}))
    (session/"spec/architecture.json").write_text(json.dumps({"decisions":[{"decision_id":"ADR-1","title":"Progressive web app","decision":"Build a PWA","rationale":"Browser-first scope","options_considered":[{"option":"Native","reason":"Outside first release"}]}],"components":[{"name":"Task service","responsibility":"Track work"}]}))
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
    assert page.get_by_text("In progress").count() == 1
    assert page.get_by_text("Complete").count() == 1


def test_not_started_running_and_done_pages(page, ui):
    page.goto(ui + "/tasks/empty/view")
    assert page.get_by_text("This task hasn't started.").is_visible()
    page.goto(ui + "/tasks/live/view")
    assert page.get_by_text("Live engine output").is_visible()
    assert page.get_by_text("Generating CONOPS option 2/3").count() == 1
    page.goto(ui + "/tasks/done/view")
    assert page.get_by_text("Artifacts", exact=True).is_visible()
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
    waiting=page.locator('.gate.waiting')
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
            page.get_by_role("button",name="Logout").click()
            page.get_by_text("Sign in",exact=True).wait_for()
            assert page.get_by_role("link",name="google").is_visible()
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
    page.goto(ui+"/sessions")
    page.get_by_text("Family task evidence",exact=True).wait_for()
    page.screenshot(path="/downloads/vmodel-bundle-sessions.png",full_page=True)
    page.get_by_text("Family task evidence",exact=True).click()
    page.get_by_text("Intent model").wait_for()
    assert page.locator("#intent").get_by_text("Family task evidence",exact=True).is_visible()
    page.screenshot(path="/downloads/vmodel-interview-view.png",full_page=True)
    page.get_by_role("button",name="Overview").click()
    page.get_by_text("Stakeholder needs").wait_for()
    assert not page.get_by_text("Bundle integrity passed").is_visible()
    page.screenshot(path="/downloads/vmodel-bundle-overview.png",full_page=True)
    page.get_by_role("button",name="Traceability").click()
    assert page.locator(".trace-node").count()==5
    page.locator('.trace-node[data-node="R-1"]').click()
    assert page.get_by_text("3 connected edges").is_visible()
    page.screenshot(path="/downloads/vmodel-traceability-diagram.png",full_page=True)
    page.get_by_role("button",name="Architecture").click()
    assert page.get_by_text("Build a PWA").is_visible()
    page.get_by_role("button",name="Reviews + gates").click()
    assert page.get_by_text("gate.auto_decision",exact=True).is_visible()
    page.get_by_role("button",name="Oracle").click()
    assert page.get_by_text("Bundle integrity passed").is_visible()


def test_artifacts_folder_setting_validates_and_reports_sessions(page,ui):
    page.goto(ui+"/sessions");page.get_by_role("button",name="Artifacts folder").click()
    page.screenshot(path="/downloads/vmodel-artifacts-folder-setting.png",full_page=True)
    page.locator('input[name="path"]').fill('/definitely/missing/artifacts')
    page.get_by_role("button",name="Validate & load sessions").click()
    page.get_by_text("Artifacts folder could not be loaded.").wait_for()
    assert page.get_by_text("Likely cause:",exact=False).is_visible()
