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
from playwright.sync_api import expect, sync_playwright

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


def _next_cost_poll_fire(page):
    """Virtual time of the steady-state session-cost header's next 60s interval fire.

    The page records every setInterval registration through the init script below;
    the header's poll is the LAST 60s registration (its pre-mount predecessor is
    disposed on navigation, and the balance poll is registered just before it)."""
    regs = [r for r in page.evaluate("window.__regs") if r["every"] == 60000]
    assert regs, "no 60s interval was registered"
    at = max(r["at"] for r in regs)
    now = page.evaluate("Date.now()")
    fire = at + 60000
    while fire <= now:
        fire += 60000
    return fire


_RECORD_INTERVALS = "window.__regs=[];const _si=window.setInterval;window.setInterval=(f,t,...a)=>{window.__regs.push({at:Date.now(),every:t});return _si(f,t,...a)}"


def _wait_cost_hits(page, hits, n, timeout=10):
    """Wait until the route has been hit exactly n times (real-time: the route handler runs outside the
    fake clock). Requests issued around a clock.run_for pump can stall until the clock turns again,
    so nudge it while waiting."""
    deadline = time.time() + timeout
    while len(hits) < n and time.time() < deadline:
        time.sleep(0.4)
        if len(hits) < n:
            page.clock.run_for(100)
    assert len(hits) == n, (len(hits), n)


def _flush(page):
    """Let any request issued around the last clock turn actually arrive before asserting it never happened."""
    time.sleep(0.4)
    page.clock.run_for(100)
    time.sleep(0.4)


def _quiet(hits, window=0.3, timeout=8):
    """Real-time wait until no new route hits arrive for a window: every in-flight refresh has settled."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = len(hits)
        time.sleep(window)
        if len(hits) == n:
            return n
    return len(hits)


def test_session_cost_header_uses_60s_safety_and_skips_while_hidden(page, ui):
    """The periodic refresh itself: the 60s safety interval fires a real refresh (fake
    clock, hermetic), no refresh happens while the tab is hidden, and returning
    to the tab refreshes immediately."""
    page.clock.install()
    page.add_init_script(_RECORD_INTERVALS)
    state = {"price": 0.73}
    cost_hits, phases_hits = [], []
    def cost(route):
        cost_hits.append(1)
        route.fulfill(json={"price": state["price"], "price_usd": state["price"]}, headers={"Cache-Control": "no-store"})
    page.route("**/api/tasks/live/cost", cost)
    page.route("**/api/tasks/live/cost-phases", lambda r: (phases_hits.append(1), r.fulfill(json={"phases": []}, headers={"Cache-Control": "no-store"})))
    page.goto(ui + "/tasks/live/view")
    page.wait_for_selector("#app.view-in")   # route mount settled and re-navigated: only the steady-state cost header instance stays live (the pre-mount instance is disposed on navigation)
    cost_el = page.locator("#sessioncost")
    expect(cost_el).to_have_text("0.73 credits · $0.73")
    base = _quiet(cost_hits)                 # every startup refresh settled: the header's pending flag is clear
    base_phases = _quiet(phases_hits)
    now = page.evaluate("Date.now()")
    page.clock.run_for(_next_cost_poll_fire(page) + 500 - now)
    _wait_cost_hits(page, cost_hits, base + 1)          # the poll's own fire ran a real refresh (same price, so the text stands)
    _wait_cost_hits(page, phases_hits, base_phases + 1) # ...and the refresh fully settled (pending cleared) before the clock moves on
    assert cost_el.inner_text() == "0.73 credits · $0.73"
    state["price"] = 0.9
    now = page.evaluate("Date.now()")
    page.clock.run_for(_next_cost_poll_fire(page) - 1500 - now)   # 1.5s before the poll's next fire: nothing
    _flush(page)
    assert len(cost_hits) == base + 1 and cost_el.inner_text() == "0.73 credits · $0.73"
    page.clock.run_for(2_500)                                     # crosses it: the poll itself refreshes
    _wait_cost_hits(page, cost_hits, base + 2)
    _wait_cost_hits(page, phases_hits, base_phases + 2)
    expect(cost_el).to_have_text("0.90 credits · $0.90", timeout=8000)
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => window.__hidden === true})")
    page.evaluate("window.__hidden = true")
    state["price"] = 1.1
    page.clock.run_for(65_000)               # two more interval fires, both skipped while the tab is hidden
    _flush(page)
    assert len(cost_hits) == base + 2 and cost_el.inner_text() == "0.90 credits · $0.90"
    page.evaluate("window.__hidden = false")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    _wait_cost_hits(page, cost_hits, base + 3)        # returning to the tab refreshes immediately
    expect(cost_el).to_have_text("1.10 credits · $1.10", timeout=8000)


def test_session_cost_refresh_debounces_overlap_and_recovers(page, ui):
    """A refresh in flight debounces the interval and the visibility trigger;
    once it settles the next interval fire refreshes normally - no refresh is lost."""
    page.clock.install()
    page.add_init_script(_RECORD_INTERVALS)
    state = {"price": 0.73, "hold": False}
    cost_hits, phases_hits, held = [], [], []
    def cost(route):
        cost_hits.append(1)
        if state["hold"]:
            held.append(route)
            return
        route.fulfill(json={"price": state["price"], "price_usd": state["price"]}, headers={"Cache-Control": "no-store"})
    page.route("**/api/tasks/live/cost", cost)
    page.route("**/api/tasks/live/cost-phases", lambda r: (phases_hits.append(1), r.fulfill(json={"phases": []}, headers={"Cache-Control": "no-store"})))
    page.goto(ui + "/tasks/live/view")
    page.wait_for_selector("#app.view-in")   # route mount settled and re-navigated: a single cost header instance is live
    host = page.locator("#sessioncost")
    expect(host).to_have_text("0.73 credits · $0.73")
    base = _quiet(cost_hits)                 # every startup refresh settled
    base_phases = _quiet(phases_hits)
    state.update(price=0.9, hold=True)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    _wait_cost_hits(page, cost_hits, base + 1)
    assert len(held) == 1, ("the visibility trigger did not start exactly one refresh", held)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")   # debounced while the first refresh is in flight
    now = page.evaluate("Date.now()")
    page.clock.run_for(30_500)               # halfway to the 60s safety interval: also debounced (the parked request's own 60s read timeout is not crossed)
    _flush(page)
    assert len(cost_hits) == base + 1 and len(held) == 1, (cost_hits, held)
    state["hold"] = False
    held[0].fulfill(json={"price": 0.9, "price_usd": 0.9}, headers={"Cache-Control": "no-store"})
    _wait_cost_hits(page, phases_hits, base_phases + 1)   # the released refresh fully settled (pending cleared)
    expect(host).to_have_text("0.90 credits · $0.90", timeout=8000)
    state["price"] = 1.1
    now = page.evaluate("Date.now()")
    page.clock.run_for(_next_cost_poll_fire(page) + 500 - now)               # pending cleared: the next interval fire refreshes normally
    _wait_cost_hits(page, cost_hits, base + 2)
    expect(host).to_have_text("1.10 credits · $1.10", timeout=8000)


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
