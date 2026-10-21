"""The persistent SPA shell: shell nodes survive navigation, geometry is reserved before async
data and fonts arrive, history and deep links work without reloads, unknown routes get an explicit
Not Found, and leaving a page stops its polling."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"


def _port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    root = tmp_path_factory.mktemp("spa")
    db = root / "spa.db"
    env = {**os.environ, "DELIVERY_DB_PATH": str(db), "DELIVERY_DATABASE_URL": f"sqlite:///{db}",
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    seed = '''
from delivery_api.store import SQLiteStore
import os
s=SQLiteStore(os.environ["DELIVERY_DB_PATH"])
s.create_task("Live definition", "Detailed progress", task_id="live")
s.update_phase("live", "intent", "done", detail="Approved")
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


def _boxes(page):
    return page.evaluate("""()=>{const r={};for(const s of ['header','header nav','#sessioncost','#accountbalance','#identity']){
      const el=document.querySelector(s);const b=el.getBoundingClientRect();r[s]=[Math.round(b.x),Math.round(b.y),Math.round(b.width),Math.round(b.height)]}return r}""")


def test_shell_nodes_persist_across_navigation_without_reload(page, ui):
    page.goto(ui + "/"); page.wait_for_selector("header nav a.nav-link")
    page.evaluate("window.__spaMark={header:document.querySelector('header'),nav:document.querySelector('header nav'),cost:document.querySelector('#sessioncost'),footer:document.querySelector('footer'),alive:1}")
    for label in ("About", "Add Credits", "Home"):
        page.get_by_role("link", name=label, exact=True).first.click(); page.wait_for_timeout(700)
        assert page.evaluate("window.__spaMark&&window.__spaMark.alive") == 1, "navigation reloaded the document at " + label
    same = page.evaluate("""({header:document.querySelector('header')===window.__spaMark.header,
        nav:document.querySelector('header nav')===window.__spaMark.nav,
        cost:document.querySelector('#sessioncost')===window.__spaMark.cost,
        footer:document.querySelector('footer')===window.__spaMark.footer})""")
    assert all(same.values()), same


def test_geometry_is_reserved_while_auth_is_delayed(page, ui):
    held = []
    page.route("**/api/auth", lambda route: held.append(route))   # the response is parked until the test releases it
    page.goto(ui + "/")
    page.wait_for_selector("header nav a.nav-link")
    early = _boxes(page)
    assert page.locator("header .nav-ops a.nav-link").count() == 0 or not page.locator("header .nav-ops").is_visible()
    assert held, "the app never asked for auth"
    held[0].fulfill(response=held[0].fetch())   # release the real auth response
    page.wait_for_selector("#identity .avatar", timeout=8000)
    page.wait_for_timeout(400)
    late = _boxes(page)
    # every reserved slot keeps its position and width; the identity dot vs avatar differs only in
    # height inside its fixed slot (both are centered in the 84px header), which moves nothing.
    for sel in ("header", "header nav", "#sessioncost", "#accountbalance"):
        assert early[sel] == late[sel], (sel, early[sel], late[sel])
    assert early["#identity"][0] == late["#identity"][0] and early["#identity"][2] == late["#identity"][2], (early["#identity"], late["#identity"])


def test_signed_out_sign_in_fits_its_reserved_slot_and_geometry_holds(page, ui):
    for width in (1365, 390):
        page.set_viewport_size({"width": width, "height": 800})
        auth = _park(page, "**/api/auth")
        page.goto(ui + "/")
        page.wait_for_selector("header nav a.nav-link", state="attached")
        early = _boxes(page)
        assert auth, "the app never asked for auth"
        auth[0].fulfill(json={"authenticated": False, "actor": None, "admin": False, "mode": "local", "expires_at": None})
        page.wait_for_selector("#identity a.auth-link", timeout=8000)
        page.wait_for_timeout(600)
        late = _boxes(page)
        # the reserved slots keep their position and width from before auth resolved
        for sel in ("header", "header nav", "#sessioncost", "#accountbalance"):
            assert early[sel] == late[sel], (width, sel, early[sel], late[sel])
        assert early["#identity"][0] == late["#identity"][0] and early["#identity"][2] == late["#identity"][2], (width, early["#identity"], late["#identity"])
        assert "sign in" in page.locator("#identity").inner_text().lower()
        fits = page.evaluate("()=>{const slot=document.querySelector('#identity');return slot.scrollWidth<=slot.clientWidth+1}")
        assert fits, (width, "the Sign in link is clipped inside its reserved slot")
        assert page.locator("header .nav-ops a.nav-link").count() == 0 or not page.locator("header .nav-ops").is_visible()
        page.unroute("**/api/auth")


def test_geometry_survives_delayed_fonts(page, ui):
    for width in (1365, 390):
        page.set_viewport_size({"width": width, "height": 800})
        held = []
        page.route(re.compile(r"\.woff2"), lambda route: held.append(route))   # fonts parked until released below
        page.goto(ui + "/", wait_until="domcontentloaded")   # parked fonts would hold back the load event
        page.wait_for_selector("header nav a.nav-link", state="attached")
        page.wait_for_selector("#identity .avatar", timeout=8000)
        page.wait_for_timeout(700)
        early = _boxes(page)   # rendered with fallback metrics while the webfonts are parked
        assert held, "no webfont was requested"
        for route in held:
            route.fulfill(response=route.fetch())
        page.evaluate("document.fonts.ready.then(()=>{window.__fontsReady=1})")
        page.wait_for_function("window.__fontsReady===1", timeout=8000)
        page.wait_for_timeout(500)
        late = _boxes(page)
        # nothing in the header may move when the real font metrics arrive: the brand box and the
        # nav line box are fixed-width/height, so link positions are pixel-identical too
        assert early == late, (width, early, late)
        assert page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth"), width
        page.unroute(re.compile(r"\.woff2"))


def test_geometry_survives_blocked_fonts_and_the_failure_is_logged(page, ui):
    page.route(re.compile(r"\.woff2"), lambda r: r.abort())
    page.goto(ui + "/"); page.wait_for_selector("header nav a.nav-link")
    page.wait_for_timeout(600)
    early = _boxes(page)
    page.wait_for_timeout(2500)
    late = _boxes(page)
    assert early == late, (early, late)
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_back_forward_and_deep_links(page, ui):
    page.goto(ui + "/about"); page.wait_for_selector("text=ABOUT REQUI.RE")
    page.evaluate("window.__spaMark=1")
    page.get_by_role("link", name="Home", exact=True).first.click(); page.wait_for_selector("#new", timeout=8000)
    page.go_back(); page.wait_for_selector("text=ABOUT REQUI.RE")
    assert page.evaluate("window.__spaMark") == 1, "browser back reloaded the document"
    page.go_forward(); page.wait_for_selector("#new", timeout=8000)
    assert page.evaluate("window.__spaMark") == 1, "browser forward reloaded the document"


def test_an_unknown_in_app_route_gets_an_explicit_not_found(page, ui):
    page.goto(ui + "/"); page.wait_for_selector("header nav a.nav-link")
    page.evaluate("window.__spaMark=1;history.pushState({},'','/definitely-not-a-page');dispatchEvent(new PopStateEvent('popstate'))")
    page.wait_for_timeout(700)
    assert "not found" in page.locator("#app").inner_text().lower()
    assert page.evaluate("window.__spaMark") == 1 and page.locator("header nav a.nav-link").count() > 0


def test_leaving_a_page_stops_its_polling(page, ui):
    seen = []
    page.on("request", lambda req: seen.append(req.url) if "/api/tasks/live" in req.url else None)
    page.goto(ui + "/tasks/live/view"); page.wait_for_timeout(2600)
    during = len(seen)
    assert during > 0, "the task page did not poll while open"
    seen.clear()
    page.get_by_role("link", name="About", exact=True).first.click()
    page.wait_for_selector("text=ABOUT REQUI.RE"); page.wait_for_timeout(3200)
    assert seen == [], "the old page kept fetching after navigation: " + json.dumps(seen[:3])


@pytest.mark.parametrize("width", [320, 360, 480])
def test_narrow_widths_have_no_horizontal_overflow(page, ui, width):
    page.set_viewport_size({"width": width, "height": 740})
    page.goto(ui + "/"); page.wait_for_selector("header nav a.nav-link", state="attached")
    page.wait_for_timeout(1200)
    assert page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth"), width


def test_vendored_assets_match_the_recorded_hashes():
    for line in (WEB / "vendor/SHA256SUMS").read_text().splitlines():
        if not line.strip(): continue
        want, name = line.split()[0], line.split()[-1]
        got = hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest()
        assert got == want, name


def _park(page, pattern):
    held = []
    page.route(pattern, lambda route: held.append(route))
    return held


def test_geometry_is_reserved_while_cost_and_balance_are_delayed(page, ui):
    cost = _park(page, "**/api/tasks/*/cost")
    balance = _park(page, "**/api/account/credits")
    page.goto(ui + "/tasks/live/view")
    page.wait_for_selector("header nav a.nav-link")
    page.wait_for_selector("#identity .avatar", timeout=8000)
    early = _boxes(page)
    assert cost and balance, "the page never asked for cost/balance"
    for held in (cost[0], balance[0]):
        held.fulfill(response=held.fetch())
    page.wait_for_timeout(1500)
    late = _boxes(page)
    for sel in ("header", "header nav", "#sessioncost", "#accountbalance", "#identity"):
        assert early[sel][0] == late[sel][0] and early[sel][2] == late[sel][2], (sel, early[sel], late[sel])


def test_a_non_admin_never_gets_operator_links_and_the_shell_stays_put(page, ui):
    auth = _park(page, "**/api/auth")
    page.goto(ui + "/")
    page.wait_for_selector("header nav a.nav-link")
    early = _boxes(page)
    assert auth, "the app never asked for auth"
    auth[0].fulfill(json={"authenticated": True, "actor": "local user", "admin": False, "mode": "local", "expires_at": None})
    page.wait_for_timeout(1200)
    assert page.locator("header .nav-ops").is_hidden(), "operator links are visible to a non-admin"
    late = _boxes(page)
    for sel in ("header", "header nav", "#sessioncost", "#accountbalance"):
        assert early[sel] == late[sel], (sel, early[sel], late[sel])
    assert early["#identity"][0] == late["#identity"][0] and early["#identity"][2] == late["#identity"][2]


def test_operator_views_mount_inside_the_shell_and_hash_navigation_does_not_remount(page, ui):
    page.goto(ui + "/")
    page.wait_for_selector("header nav a.nav-link")
    page.evaluate("window.__spaMark={header:document.querySelector('header'),alive:1}")
    page.locator("header .nav-ops a.nav-link", has_text="Server Health").click()
    page.wait_for_selector("text=Server Health", timeout=8000)
    assert page.evaluate("window.__spaMark.alive") == 1
    frame = page.locator("#app").inner_text()
    assert "Log Window" in frame or "Server Health" in frame
    page.locator("header .nav-ops a.nav-link", has_text="Admin Stops").click()
    page.wait_for_timeout(900)
    assert page.evaluate("window.__spaMark.alive") == 1, "hash navigation reloaded the document"
    assert page.evaluate("document.querySelector('header')===window.__spaMark.header")
    assert page.url.endswith("/ops#stops")
    # and back into a main page, still inside the same shell
    page.get_by_role("link", name="Home", exact=True).first.click()
    page.wait_for_selector("#new", timeout=8000)
    assert page.evaluate("document.querySelector('header')===window.__spaMark.header")


def test_the_identity_slot_shows_each_state_without_clipping(page, ui):
    page.goto(ui + "/")
    page.wait_for_selector("#identity .avatar", timeout=8000)
    fits = page.evaluate("""()=>{const slot=document.querySelector('#identity');
      return slot.scrollWidth<=slot.clientWidth+1 && slot.getBoundingClientRect().width>=34}""")
    assert fits, "the identity slot clips its content"
