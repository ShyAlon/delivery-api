"""One header for every page: the markup lives in web/shell-header.html and is put into each page when it is served; the avatar and nav rules cannot be
overridden by a page's own styles."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
HEADER = (WEB / "shell-header.html").read_text(encoding="utf-8").strip()


def test_no_page_file_carries_its_own_header():
    for name in ("index.html", "ops.html"):
        text = (WEB / name).read_text(encoding="utf-8")
        assert "<header" not in text and text.count("<!--SHELL_HEADER-->") == 1, name


def test_the_shared_header_has_the_slots_every_page_needs():
    assert HEADER.count("<header>") == 1 and HEADER.count("<nav>") == 1
    for needle in ('id="sessioncost"', 'id="identity"', 'class="brand"', 'href="/credits"', 'href="/about"'):
        assert needle in HEADER, needle


@pytest.mark.parametrize("path", ["/", "/about", "/credits", "/ops"])
def test_every_page_route_serves_exactly_the_same_header(path):
    import delivery_api.app as module
    response = TestClient(module.app).get(path, headers={"Accept": "text/html"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html"), path
    body = response.text
    assert body.count("<header>") == 1 and "SHELL_HEADER" not in body and HEADER in body


def test_the_task_and_session_page_handlers_return_the_assembled_page():
    import delivery_api.app as module
    pages = [module.task_view("t"), module.gate_page("t", "g"), module.complete_page("t"), module.decision_page("t", "w")]
    for page in pages:
        assert HEADER in page.body.decode() and "SHELL_HEADER" not in page.body.decode()


def test_the_routes_serve_the_assembled_page_for_both_files():
    import delivery_api.app as module
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert 'FileResponse(WEB_DIR / "index.html")' not in src and 'FileResponse(WEB_DIR / "ops.html"' not in src
    assert "app" in dir(module) and HEADER in module._page("index.html").body.decode() and HEADER in module._page("ops.html").body.decode()


def _rules(css):
    return re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_the_avatar_has_no_padding_and_no_page_rule_reaches_header_buttons():
    css = (WEB / "chrome.css").read_text(encoding="utf-8")
    avatar = [body for sel, body in _rules(css) if sel.strip() == "header .avatar"]
    assert avatar and "padding:0" in avatar[0] and "line-height:1" in avatar[0]
    for sel, _ in _rules(css):
        for one in sel.split(","):
            if "body.ops-page" in one and re.search(r"\bbutton\b", one):
                assert "#frame" in one, "an operator button rule must stay inside #frame: " + one.strip()


def test_only_the_nav_takes_free_space_and_an_empty_cost_slot_disappears():
    css = (WEB / "chrome.css").read_text(encoding="utf-8")
    bodies = {sel.strip(): body for sel, body in _rules(css)}
    assert "margin-left:0" in bodies["header .session-cost"]
    assert "display:none" in bodies["header .session-cost:empty"]
