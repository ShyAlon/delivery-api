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
# Every test file belongs to exactly one deterministic shard. No test filters
# vary by shard; collection-only gate tests remain in their assigned file.
mapfile -t CORE_FILES < <("$PYTHON" - <<'PYSHARD'
import os
from pathlib import Path
index=int(os.environ.get('DELIVERY_CORE_SHARD','0'))
count=int(os.environ.get('DELIVERY_CORE_SHARDS','1'))
if not 0<=index<count:raise SystemExit('invalid core shard')
files=sorted(p for p in Path('tests').rglob('test_*.py') if p.name!='test_ui_playwright.py')
for n,path in enumerate(files):
    if n%count==index:print(path)
PYSHARD
)
"$PYTHON" -m pytest -v "${CORE_FILES[@]}" -m 'not engine_integration and not producer_integration and not ui_cosmetic' --maxfail=3 --timeout=60
# One shard owns the unchanged browser gate, after its core files complete.
if [[ "${DELIVERY_CORE_SHARD:-0}" != 0 ]]; then exit 0; fi
# ui_cosmetic browser tests are tracked debt, not a merge gate: the separate
# nonblocking ui-cosmetic CI job runs them and reports honest red on failure.
if [[ "${DELIVERY_BROWSER_SCOPE:-smoke}" == full ]]; then
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py -m 'not ui_cosmetic' --maxfail=1 --timeout=45
else
  "$PYTHON" -m pytest -q tests/test_ui_playwright.py -m 'not ui_cosmetic' --maxfail=1 --timeout=45 -k 'dashboard_draws or not_started_running_and_done or basic_auth_browser or oauth_identity or gate_page or phase_rows_link or live_headline or flagged_item_review or progress_is_one_line or running_cost_header or answer_enter_sends'
fi
