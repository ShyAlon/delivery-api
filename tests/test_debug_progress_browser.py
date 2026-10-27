"""The Debug dialog must show the real build identity and honest collection progress.

The image already carries the four repository SHAs in REQUI_BUILD_REPOS; /health
now returns them in tenant mode too, and the dialog shows them instead of the
opaque runtime id. Downloading the everything ZIP reports progress per section
and finishes with an honest count, naming any section that could not load
instead of silently dropping it.
"""
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

REPOS = "delivery-api=abcdef12,interview-engine=12345678,spec-generator=90abcdef,verification-loop=abcdef01"
LABEL = "delivery-api abcdef12, interview-engine 12345678, spec-generator 90abcdef, verification-loop abcdef01"


def _port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    root = tmp_path_factory.mktemp("debug")
    db = root / "debug.db"
    env = {**os.environ, "DELIVERY_DB_PATH": str(db), "DELIVERY_DATABASE_URL": f"sqlite:///{db}",
           "REQUI_BUILD_ID": "local-9", "REQUI_BUILD_REPOS": REPOS,
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    seed = '''
from delivery_api.store import SQLiteStore
import os
s=SQLiteStore(os.environ["DELIVERY_DB_PATH"])
s.create_task("Debug subject", "Diagnostics target", task_id="dbg")
s.update_phase("dbg", "intent", "done", detail="Approved")
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
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        yield page
        browser.close()


def _open_debug(page, ui):
    page.goto(ui + "/tasks/dbg/view")
    page.locator("#accountbutton").wait_for(timeout=10000)
    page.locator("#accountbutton").click()
    page.locator("#opendebug").click()
    page.locator(".debug-dialog").wait_for(timeout=5000)


def test_debug_dialog_shows_repository_build_identity(page, ui):
    _open_debug(page, ui)
    badge = page.locator(".debug-dialog .runtime-id")
    badge.wait_for(timeout=5000)
    page.wait_for_function(
        "() => document.querySelector('.debug-dialog .runtime-id').textContent.includes('delivery-api abcdef12')",
        timeout=10000)
    assert badge.inner_text() == f"BUILD {LABEL}"
    page.locator(".debug-dialog .dialog-x").click()


def test_everything_zip_reports_per_section_progress_and_honest_counts(page, ui, tmp_path):
    def slow_offline_logs(route):
        time.sleep(0.8)  # hold the section so the per-section progress line is observable
        route.fulfill(status=500, content_type="application/json", body='{"detail":"log store offline"}')
    page.route("**/api/server-log*", slow_offline_logs)
    _open_debug(page, ui)
    with page.expect_download(timeout=15000) as dl:
        page.locator("#downloadeverything").click()
        # Per-section progress while collecting. The section INDEX is not pinned:
        # sections come and go (Request Timing joined 2026-10-26) - pin the name.
        page.wait_for_function(
            "() => {const t=document.querySelector('#debugstatus').textContent;return t.includes('Collecting section')&&t.includes('Server logs (last 30 min)')}",
            timeout=10000)
    status = page.locator("#debugstatus")
    page.wait_for_function(
        "() => document.querySelector('#debugstatus').textContent.startsWith('Downloaded requi-everything.zip')",
        timeout=10000)
    text = status.inner_text()
    # This fixture collects 8 sections; exactly two fail honestly: the fixture task
    # never ran an engine (its diagnostics section reports unavailable) and the
    # server-logs route is held to a 500 above. Section titles contain commas, so
    # pin the whole unavailable clause instead of splitting it.
    assert "Downloaded requi-everything.zip: 6 of 8 sections collected; unavailable: Task diagnostics (question, control state, engine state), Server logs (last 30 min)." in text, text
    download = dl.value
    target = tmp_path / "everything.zip"
    download.save_as(target)
    with zipfile.ZipFile(target) as z:
        body = z.read("requi-everything.txt").decode()
    assert "===== Known omissions =====" in body
    assert "===== Server logs (last 30 min) =====" in body
    assert '"error"' in body  # the failed section is inline, not silently dropped
    assert "===== Client logs (this tab) =====" in body
    page.locator(".debug-dialog .dialog-x").click()


def test_copy_summary_uses_the_repository_build_identity(page, ui):
    page.route("**/api/tasks/dbg/diagnostics", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"question":"q","control_state":{}}'))
    _open_debug(page, ui)
    page.locator("#copysummary").click()
    page.wait_for_function(
        "() => document.querySelector('#debugstatus').textContent.startsWith('Diagnostic summary copied')",
        timeout=10000)
    copied = page.evaluate("navigator.clipboard.readText()")
    assert f"build: {LABEL}" in copied
    page.locator(".debug-dialog .dialog-x").click()
