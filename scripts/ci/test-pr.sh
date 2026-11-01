#!/usr/bin/env bash
# Bounded PR check: full delivery core plus browser boundary coverage.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON=.venv/bin/python
export REQUI_TEST_ARTIFACTS="${REQUI_TEST_ARTIFACTS:-${RUNNER_TEMP:-/tmp}/requi-test-artifacts}"
mkdir -p "$REQUI_TEST_ARTIFACTS"
sha256sum --check vendor/acceptance-producers.sha256
"$PYTHON" -m pip install vendor/interview_engine-0.1.0-py3-none-any.whl vendor/spec_generator-0.1.0-py3-none-any.whl
# Cross-repo engine integrations require the explicit full local release check.
"$PYTHON" -m pytest -q --ignore=tests/test_ui_playwright.py -m 'not engine_integration and not producer_integration and not ui_cosmetic' --maxfail=3
# ui_cosmetic browser tests are tracked debt, not a merge gate: the separate
# nonblocking ui-cosmetic CI job runs them and reports honest red on failure.
if [[ "${DELIVERY_BROWSER_SCOPE:-smoke}" == full ]]; then
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py -m 'not ui_cosmetic' --maxfail=1 --timeout=45
else
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py -m 'not ui_cosmetic' --maxfail=1 --timeout=45 -k 'dashboard_draws or not_started_running_and_done or basic_auth_browser or oauth_identity or gate_page or phase_rows_link or live_headline or flagged_item_review or progress_is_one_line or running_cost_header or answer_enter_sends'
fi
