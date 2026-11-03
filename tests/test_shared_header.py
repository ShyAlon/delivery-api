"""One header for every page: it is the persistent SPA shell (spa-shell.js), mounted once by the app page.
Every HTML route serves the same app page, so the header, navigation, slots and footer are identical by
construction; the avatar and operator-scoped style rules cannot be overridden by a page's own styles."""
import re
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
SHELL = (WEB / "page-component.js").read_text(encoding="utf-8")


def test_no_page_file_carries_its_own_header():
    # the shell builds the header at runtime; the served page must not carry a static one
    text = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<header" not in text and '<div id="shell-top">' in text and 'id="page-outlet"' in text
    assert not (WEB / "shell-header.html").exists() and not (WEB / "ops.html").exists()


def test_the_shell_has_the_slots_every_page_needs():
    for needle in ('id="sessioncost"', 'id="accountbalance"', 'id="identity"', 'class="brand"', "href:'/credits'", "href:'/about'", '<header>', '<nav', '<footer'):
        assert needle in SHELL, needle


@pytest.mark.parametrize("path", ["/", "/about", "/credits", "/ops"])
def test_every_page_route_serves_exactly_the_same_app_page(path):
    import delivery_api.app as module
    response = TestClient(module.app).get(path, headers={"Accept": "text/html"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html"), path
    body = response.text
    assert '<div id="shell-top">' in body and "/assets/spa-shell.js" in body and "<!--REQUI_CONFIG-->" not in body and "window.__REQUI_CONFIG__" in body
    assert body == TestClient(module.app).get("/", headers={"Accept": "text/html"}).text


def test_the_task_and_session_page_handlers_return_the_app_page():
    import delivery_api.app as module
    pages = [module.task_view("t"), module.gate_page("t", "g"), module.complete_page("t"), module.decision_page("t", "w")]
    for page in pages:
        body = page.body.decode()
        assert '<div id="shell-top">' in body and 'id="page-outlet"' in body and "SHELL_HEADER" not in body


def test_the_routes_serve_the_app_page_and_the_operator_page_is_the_same_document():
    import delivery_api.app as module
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert 'FileResponse(WEB_DIR / "index.html")' not in src and "ops.html" not in src
    assert "app" in dir(module) and '<div id="shell-top">' in module._page("index.html").body.decode()
    assert '<div id="shell-top">' in module.operator_page().body.decode()


def _rules(css):
    return re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_the_avatar_has_no_padding_and_no_page_rule_reaches_header_buttons():
    css = json.loads(re.search(r"const pageStyles=(.*?);\n", SHELL).group(1))["/assets/chrome.css"]
    avatar = [body for sel, body in _rules(css) if sel.strip() == "header .avatar"]
    assert avatar and "padding:0" in avatar[0] and "line-height:1" in avatar[0]
    for sel, _ in _rules(css):
        for one in sel.split(","):
            if "body.ops-page" in one and re.search(r"\bbutton\b", one):
                assert "#frame" in one, "an operator button rule must stay inside #frame: " + one.strip()


def test_only_the_nav_is_flexible_and_the_cost_slot_keeps_its_space():
    css = json.loads(re.search(r"const pageStyles=(.*?);\n", SHELL).group(1))["/assets/chrome.css"]
    bodies = {sel.strip(): body for sel, body in _rules(css)}
    # the header is a grid where only the nav column flexes; the cost/balance slots keep their
    # reserved space whether or not data has arrived (visibility, never display), so nothing moves.
    assert "grid-template-columns:auto 1fr auto auto auto" in bodies["header"]
    assert "display:block" in bodies["header .session-cost:empty"]
    assert "visibility:hidden" in bodies["header .session-cost[hidden]"]
