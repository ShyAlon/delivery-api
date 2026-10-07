#!/usr/bin/env bash
# Bounded PR check: full delivery core plus browser boundary coverage.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON=.venv/bin/python
# Cross-repo engine integrations require the explicit full local release check.
"$PYTHON" -m pytest -q --ignore=tests/test_ui_playwright.py -m 'not engine_integration and not producer_integration' --maxfail=3
if [[ "${DELIVERY_BROWSER_SCOPE:-smoke}" == full ]]; then
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py --maxfail=1 --timeout=45
else
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py --maxfail=1 --timeout=45 -k 'dashboard_draws or not_started_running_and_done or basic_auth_browser or oauth_identity or gate_page or phase_rows_link or live_headline or flagged_item_review or progress_is_one_line or running_cost_header or answer_enter_sends'
fi
