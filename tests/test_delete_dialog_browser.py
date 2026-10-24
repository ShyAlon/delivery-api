"""Deleting a task from its own page must close the dialog when leaving the page.

The delete dialog lives in the page-level #modal container, a sibling of #app.
Navigating from the task page back to the dashboard swaps only #app, so before
the fix the "Deleting…" dialog stayed on screen forever after a successful
delete. The confirm path now closes the modal before navigating.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


def _port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    root = tmp_path_factory.mktemp("delete")
    db = root / "delete.db"
    env = {**os.environ, "DELIVERY_DB_PATH": str(db), "DELIVERY_DATABASE_URL": f"sqlite:///{db}",
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    seed = '''
from delivery_api.store import SQLiteStore
import os
s=SQLiteStore(os.environ["DELIVERY_DB_PATH"])
s.create_task("Doomed definition", "To be deleted", task_id="doomed")
s.update_phase("doomed", "intent", "done", detail="Approved")
'''
    subprocess.run([sys.executable, "-c", seed], check=True, env=env)
    port = _port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "delivery_api.app:app", "--host", "127.0.0.1", "--port", str(port)], env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health", timeout=.2); break
        except Exception: time.sleep(.1)
    else: raise RuntimeError("UI server did not start")
    yield base
    proc.terminate(); proc.wait(timeout=5)


@pytest.fixture()
def page(ui):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        yield page
        browser.close()


def test_delete_from_task_page_closes_the_dialog(page, ui):
    # No mocks: the real sqlite store deletes the task, so the dashboard must
    # both lose the stuck dialog AND no longer list the deleted task.
    page.goto(ui + "/tasks/doomed/view")
    page.locator("#deletetask").wait_for(timeout=10000)
    page.locator("#deletetask").click()
    dialog = page.locator("#modal .dialog")
    dialog.wait_for(timeout=5000)
    assert "Doomed definition" in dialog.inner_text()
    page.locator("#confirmdelete").click()
    page.wait_for_url(ui + "/", timeout=10000)
    # The modal must be closed and empty: no stuck "Deleting…" dialog on the dashboard.
    page.wait_for_function("() => { const m = document.querySelector('#modal'); return m.classList.contains('hidden') && m.innerHTML === '' }", timeout=5000)
    assert page.locator("#modal .dialog").count() == 0
    # The deletion is real: the dashboard no longer lists the task.
    assert "Doomed definition" not in page.locator("#app").inner_text()
