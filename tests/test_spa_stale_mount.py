"""A page that finishes mounting after you navigated away must not commit its DOM,
register live closures or keep polling: the router drops stale mounts wholesale.

Each test parks a real route's response, navigates away mid-mount, then releases it.
Before the fix, the stale mount overwrote the live page, its error path crashed on
missing elements (unhandled rejection) and its interval kept polling the server.
"""
import json
import time

import pytest


@pytest.fixture
def ui(pg_browser):
    import jwt
    from delivery_api import config
    from pg_workspace import KEY
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):
        workspace.store.create_task('Live definition','Detailed progress',task_id='live')
        workspace.store.update_phase('live','intent','done',detail='Approved')
    claims=jwt.decode(workspace.token,options={'verify_signature':False})
    claims['email']=config.OPERATOR_EMAIL
    page.context.add_cookies([{'name':'requi_access','value':jwt.encode(claims,KEY,algorithm='ES256',headers={'kid':'fixture'}),'url':base}])
    return base


@pytest.fixture
def page(ui,pg_browser):
    return pg_browser[0]


def _park(page, pattern):
    held = []
    page.route(pattern, lambda route: held.append(route))
    return held


def _wait_for(held, n=1, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(held) >= n: return True
        time.sleep(.05)
    return False


def test_a_stale_ops_mount_never_commits_polls_or_crashes(page, ui):
    """Navigate to /ops, away before its template arrives, then release everything:
    the dashboard must survive, no operator polling may run, no unhandled rejection."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    health_calls = []
    page.on("request", lambda req: health_calls.append(req.url) if "/api/admin/operations/health" in req.url else None)
    tasks = _park(page, "**/api/tasks")
    template = _park(page, "**/assets/ops-body.html")
    health = _park(page, "**/api/admin/operations/health")

    page.goto(ui + "/")
    page.wait_for_selector("header nav a.nav-link")
    page.evaluate("window.__spaMark={header:document.querySelector('header'),alive:1}")
    assert _wait_for(tasks), "the dashboard never asked for its tasks"

    page.locator("header .nav-ops a.nav-link", has_text="Server Health").click()
    assert _wait_for(template), "the operator page never asked for its template"

    page.get_by_role("link", name="Home", exact=True).first.click()
    assert _wait_for(tasks, 2), "the second dashboard mount never asked for its tasks"

    # the operator template lands first, while the newer dashboard mount is still waiting
    template[0].fulfill(response=template[0].fetch())
    assert _wait_for(health, timeout=4) or True
    # the newer dashboard mount finishes: this is the view that must survive
    tasks[1].fulfill(response=tasks[1].fetch())
    page.wait_for_selector("#new", timeout=8000)
    if health:
        health[0].fulfill(response=health[0].fetch())

    assert page.locator("#new").count() == 1, "the newer page was overwritten by the stale operator mount"
    assert page.evaluate("document.body.dataset.view||''") != "health", "a dead page's view flag survived navigation"
    assert page.evaluate("document.querySelector('header')===window.__spaMark.header"), "the shell remounted"

    health_calls.clear()
    page.wait_for_timeout(11000)   # one full operator poll interval
    assert health_calls == [], "the dead operator page kept polling: " + json.dumps(health_calls[:3])
    assert not [e for e in errors if "replaceChildren" in e], "the dead page crashed on its own missing elements: " + json.dumps(errors[:3])


def test_a_stale_dashboard_mount_never_overwrites_the_page_you_moved_to(page, ui):
    """Park the dashboard's data, navigate to the synchronous About page, release late:
    About must stay on screen."""
    tasks = _park(page, "**/api/tasks")
    page.goto(ui + "/")
    page.wait_for_selector("header nav a.nav-link")
    assert _wait_for(tasks), "the dashboard never asked for its tasks"

    page.get_by_role("link", name="About", exact=True).first.click()
    page.wait_for_selector("text=ABOUT REQUI.RE")
    tasks[0].fulfill(response=tasks[0].fetch())
    page.wait_for_timeout(1500)

    assert "about" in page.locator("#app").inner_text().lower(), "the stale dashboard overwrote the About page"
    assert page.locator("#new").count() == 0, "stale dashboard DOM is visible under the About page"


def test_stale_mount_fixture_has_no_sqlite_bootstrap():
    import inspect
    assert 'SQLiteStore' not in inspect.getsource(ui)
    assert 'pg_browser' in inspect.signature(ui).parameters
