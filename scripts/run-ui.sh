#!/usr/bin/env bash
# Start the local V-model web UI from any working directory.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${DELIVERY_HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="${PYTHON:-}"

if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then PYTHON="$(command -v python3)"
  else
    echo "Error: Python 3 was not found."
    echo "Likely cause: Python is not installed or is not on PATH."
    echo "Next: install Python 3.10+, then rerun scripts/run-ui.sh."
    exit 1
  fi
fi
# Editable installs do not automatically sync new runtime dependencies after a
# pull. Check the full import surface, then refresh the existing venv when any
# dependency is absent. This includes the pinned cross-repo contracts API: an
# editable delivery-api import alone can succeed while vmodel-contracts in the
# same venv is stale after a pull.
if ! "$PYTHON" -c 'import delivery_api,uvicorn,multipart; from vmodel_contracts import CANONICAL_STAGE_IDS; assert \"scope_tiebreak\" in CANONICAL_STAGE_IDS' >/dev/null 2>&1; then
  echo "== syncing delivery-api and UI dependencies into .venv =="
  if [ ! -x "$ROOT/.venv/bin/python" ]; then "$PYTHON" -m venv "$ROOT/.venv"; fi
  PYTHON="$ROOT/.venv/bin/python"
  if ! "$PYTHON" -m pip install -e "$ROOT"; then
    echo "Error: delivery-api dependencies could not be installed."
    echo "Likely cause: package download failed or this Python is older than 3.10."
    echo "Next: check the network and '$PYTHON --version', then rerun this script."
    exit 1
  fi
fi
cd "$ROOT"
export DELIVERY_BASIC_AUTH_ENABLED="${DELIVERY_BASIC_AUTH_ENABLED:-1}"
echo "V-model UI: http://$HOST:$PORT"
echo "Data: ${DELIVERY_DATABASE_URL:-${DELIVERY_DB_PATH:-sqlite:///delivery.db}}"
echo "Stop: Ctrl-C"
exec "$PYTHON" -m delivery_api
