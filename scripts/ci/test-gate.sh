#!/usr/bin/env bash
# Fast every-PR gate: deterministic non-Postgres, non-browser core.
# Heavy files (classify_tests.py heavy) run in the full matrix: dispatch,
# nightly schedule, and ready_for_review. No Playwright install in this scope.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON=.venv/bin/python
export REQUI_TEST_ARTIFACTS="${REQUI_TEST_ARTIFACTS:-${RUNNER_TEMP:-/tmp}/requi-test-artifacts}"
mkdir -p "$REQUI_TEST_ARTIFACTS"
sha256sum --check vendor/acceptance-producers.sha256
"$PYTHON" -m pip install vendor/interview_engine-0.1.0-py3-none-any.whl vendor/spec_generator-0.1.0-py3-none-any.whl
mapfile -t FAST_FILES < <("$PYTHON" scripts/ci/classify_tests.py fast)
"$PYTHON" -m pytest -v "${FAST_FILES[@]}" -m 'not engine_integration and not producer_integration and not ui_cosmetic' --maxfail=3 --timeout=60
