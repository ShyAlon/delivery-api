"""Local-mode-only stage rerun UI contracts, not signed tenant coverage.

Retained verbatim by owner decision on 2026-10-28. This isolated legacy SQLite
fixture is a remaining retirement consumer, not a PG or production assertion.
"""
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


def test_rerun_preview_explains_archive_and_audit_has_local_actor(page, ui):
    page.goto(ui + "/tasks/done/view")
    page.get_by_role("button", name="Rerun stage").click()
    assert page.get_by_text("Downstream artifacts will turn stale immediately", exact=False).is_visible()
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Audit").click()
    page.get_by_text("Task audit").wait_for()
    assert "local user" in page.locator(".dialog").inner_text()


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

