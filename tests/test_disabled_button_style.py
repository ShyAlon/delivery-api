"""One shared disabled-button style: not-allowed cursor, no hover animation.

Shy observed the disabled Start Interview button shows the not-allowed cursor
and stays inert on hover, while other disabled buttons still animate. Cause:
#startengine had an ID-scoped :disabled patch that out-specified every class
:hover rule, while all other buttons relied on the shared `button:disabled`
rule (specificity 0,1,1), which loses to `.primary:hover` (0,2,0) and friends.
The fix is ONE shared rule with :hover/:active variants; the per-button and
per-page patches are deleted; base rules that would fight the guard are scoped
:not(:disabled). These tests pin that contract without launching a browser.
"""
import re
import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
STYLE = (WEB / "style.css").read_text()
CHROME = (WEB / "chrome.css").read_text()


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)



def _shared_block():
    m = re.search(r"([^{}]+)\{([^{}]*cursor:not-allowed[^{}]*)\}", _strip_comments(STYLE))
    assert m, "style.css must carry one shared not-allowed disabled rule"
    return m.group(1), m.group(2).replace(" ", "")


def test_shared_rule_covers_rest_hover_and_active_states():
    selectors, _ = _shared_block()
    names = {s.strip() for s in selectors.split(",")}
    for needed in ("button:disabled", "button:disabled:hover", "button:disabled:active"):
        assert needed in names, needed


def test_shared_rule_pins_cursor_and_kills_hover_motion():
    _, body = _shared_block()
    for decl in ("cursor:not-allowed", "transform:none", "box-shadow:none", "opacity:.5", "transition:none"):
        assert decl in body, decl


def test_no_per_button_or_per_page_disabled_patches_remain():
    for bad in ("#startengine:disabled", ".gate-page-actions button:disabled"):
        assert bad not in STYLE, bad
    assert ":disabled" not in CHROME, "chrome.css must rely on the shared rule"


def test_competing_base_rules_are_scoped_not_disabled():
    assert ".phase-actions button:not(:disabled):hover" in STYLE
    ops = re.search(r"body\.ops-page #frame button[^{]*\{([^}]*)\}", CHROME)
    assert ops, "the ops chrome button rule must remain"
    assert "cursor" not in ops.group(1), (
        "an ID-scoped cursor:pointer beats button:disabled; the shared "
        "style.css button{cursor:pointer} rule already covers ops buttons"
    )


def test_shared_rule_wins_source_order_over_hover_motion_rules():
    shared_at = STYLE.index("button:disabled:hover")
    for rival in (".primary:hover", ".phase-actions button", ".avatar:hover"):
        assert STYLE.index(rival) < shared_at, rival


def test_busy_buttons_keep_progress_cursor():
    at = STYLE.index("button.is-busy")
    m = re.search(r"button\.is-busy[^{]*\{([^}]*)\}", STYLE[at:])
    assert m and "cursor:progress" in m.group(1).replace(" ", "")
    assert "button.is-busy:hover" in STYLE[at:at + 80], "busy must keep progress cursor even under the shared :disabled:hover variant"
    assert at > STYLE.index("button:disabled:hover"), "is-busy must follow the shared rule to win the specificity tie"


def test_late_injected_dialog_hover_is_scoped_to_enabled_buttons():
    source = (WEB / "shared-dialog.js").read_text()
    css = json.loads(re.search(r"const dialogStyles=(.*?);\n", source).group(1))
    assert ".dialog-x:not(:disabled):hover{" in css
    assert ".dialog-x:hover{" not in css
