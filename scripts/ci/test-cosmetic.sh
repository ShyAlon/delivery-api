#!/usr/bin/env bash
# Cosmetic browser debt: visual-polish assertions marked ui_cosmetic (brand,
# layout ratios, decorative styling - nothing that blocks an action, hides
# required information, or misleads about state or cost). This selection is
# the exact complement of the merge gate's browser runs: it reports honest
# red on failure, but it is tracked debt, not a merge blocker, and it never
# runs inside the gating job. The full local release check (plain pytest)
# still runs every one of these tests.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON=.venv/bin/python
"$PYTHON" -m pytest -q tests/test_ui_playwright.py tests/test_spa_shell.py tests/test_decision_pages_browser.py -m ui_cosmetic --timeout=45
