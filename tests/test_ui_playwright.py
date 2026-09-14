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
    env = {**os.environ, "DELIVERY_DB_PATH": str(db),
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
