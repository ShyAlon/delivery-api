"""Bookkeeping for the ui_cosmetic tier, without launching a browser.

The cosmetic tier is exactly the declared set of visual-polish browser tests:
nothing more may carry the mark (no silent broadening of the nonblocking
tier), nothing less (no cosmetic test silently untracked), and no merge-gating
smoke pattern may match a marked test. The selections in scripts/ci/test-pr.sh
and scripts/ci/test-cosmetic.sh are disjoint and exhaustive by construction
(-m 'not ui_cosmetic' vs -m ui_cosmetic over the same files); this test pins
the membership both selections depend on.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER_FILES = [
    "tests/test_ui_playwright.py",
    "tests/test_spa_shell.py",
    "tests/test_decision_pages_browser.py",
]

DECLARED = {
    "tests/test_ui_playwright.py::test_require_brand_is_consistent",
    "tests/test_ui_playwright.py::test_avatar_menu_does_not_emit_native_runtime_tooltip",
    "tests/test_ui_playwright.py::test_progress_slot_keys_render_as_title_case",
    "tests/test_ui_playwright.py::test_question_comment_columns_follow_character_ratio",
}


def _collect(marker_expr):
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", marker_expr, "-p", "no:cacheprovider", *BROWSER_FILES],
        # Nested pytest owns its own disposable server; inheriting the outer
        # fixture DSN incorrectly looks like an external database at bootstrap.
        env={k:v for k,v in os.environ.items() if k != 'DELIVERY_TEST_POSTGRES_DSN'},
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert out.returncode == 0, out.stderr + out.stdout
    return {line.strip() for line in out.stdout.splitlines() if "::" in line}


def test_ui_cosmetic_marker_is_registered():
    config = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'"ui_cosmetic: [^"]+"', config), "ui_cosmetic must be a registered marker"


def test_ui_cosmetic_membership_is_exactly_the_declared_set():
    marked = _collect("ui_cosmetic")
    extra = marked - DECLARED
    missing = DECLARED - marked
    assert not extra, "ui_cosmetic broadened beyond the declared polish set: " + ", ".join(sorted(extra))
    assert not missing, "declared cosmetic tests lost their mark: " + ", ".join(sorted(missing))


def test_no_pr_smoke_pattern_matches_a_cosmetic_test():
    script = (ROOT / "scripts/ci/test-pr.sh").read_text()
    match = re.search(r"-k '([^']+)'", script)
    assert match, "the PR smoke -k expression must stay visible in scripts/ci/test-pr.sh"
    patterns = [part.strip() for part in match.group(1).split(" or ")]
    assert len(patterns) >= 10, patterns
    marked = _collect("ui_cosmetic")
    for pattern in patterns:
        hits = [test_id for test_id in marked if pattern in test_id]
        assert not hits, f"smoke pattern {pattern!r} would deselect gating coverage via {hits}"
