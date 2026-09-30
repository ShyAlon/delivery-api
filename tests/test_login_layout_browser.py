"""The login form stays above the fold at desktop and mobile viewport sizes."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def login_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("login-layout")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "DELIVERY_DB_PATH": str(root / "login.db"),
        "DELIVERY_BASIC_AUTH_ENABLED": "1",
        "DELIVERY_COOKIE_SECURE": "0",
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "delivery_api.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=.3)
                break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError("Login test server did not start")
        yield base, root
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("width,height", [
    (1440, 900), (1366, 768), (1280, 720),
    (1024, 768), (390, 844), (375, 667),
])
def test_login_fields_and_submit_are_visible_without_scroll(login_server, width, height):
    base, root = login_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(base + "/")
            expect(page.locator("#authform")).to_be_visible()
            page.evaluate("document.fonts.ready")
            for selector in ('input[name="username"]', 'input[name="password"]', '.auth-submit'):
                bounds = page.locator(selector).bounding_box()
                assert bounds is not None, selector
                assert 0 <= bounds["x"] and bounds["x"] + bounds["width"] <= width, (selector, bounds)
                assert 0 <= bounds["y"] and bounds["y"] + bounds["height"] <= height, (selector, bounds)
            expect(page.locator(".auth-submit")).to_have_text("Sign in")
            assert page.evaluate("window.scrollY === 0 && document.documentElement.scrollWidth <= window.innerWidth")
            copy = page.locator(".auth-copy").bounding_box()
            card = page.locator(".auth-card").bounding_box()
            if width > 800:
                assert copy["x"] + copy["width"] <= card["x"]
                assert page.locator(".auth-copy").evaluate("e => getComputedStyle(e).gridRowStart") == "1"
                assert page.locator(".auth-card").evaluate("e => getComputedStyle(e).gridRowStart") == "1"
            else:
                assert card["y"] + card["height"] <= copy["y"]
            page.screenshot(path=str(root / f"login-{width}x{height}.png"))
            page.get_by_role("button", name="Create account", exact=True).click()
            expect(page.locator("#authtitle")).to_have_text("Create your account")
            expect(page.locator('input[name="password"]')).to_have_attribute("autocomplete", "new-password")
            page.get_by_role("button", name="Sign in", exact=True).first.click()
            expect(page.locator("#authtitle")).to_have_text("Sign in")
            expect(page.locator('input[name="password"]')).to_have_attribute("autocomplete", "current-password")
        finally:
            browser.close()
