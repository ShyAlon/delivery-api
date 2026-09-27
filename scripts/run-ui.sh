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
# Engine runtime precedence: explicit environment, then persisted local setting,
# then the server's deterministic venv scan/preflight.
if [ -z "${INTERVIEW_ENGINE_PYTHON:-}" ] && [ -z "${VIRTUAL_ENV:-}" ] && [ -s "$ROOT/.engine-python" ]; then
  saved_engine_python=$(sed -n '1p' "$ROOT/.engine-python")
  if [ -x "$saved_engine_python" ]; then export INTERVIEW_ENGINE_PYTHON="$saved_engine_python"; fi
fi
cd "$ROOT"
export DELIVERY_BASIC_AUTH_ENABLED="${DELIVERY_BASIC_AUTH_ENABLED:-1}"
# The local entry point serves plain http://127.0.0.1. Secure cookies are correct
# in deployment, but browsers intentionally refuse them on this local origin.
export DELIVERY_COOKIE_SECURE="${DELIVERY_COOKIE_SECURE:-0}"
echo "V-model UI: http://$HOST:$PORT"
DEFAULT_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/requi-re"
mkdir -p "$DEFAULT_DATA_HOME"
export DELIVERY_DATABASE_URL="${DELIVERY_DATABASE_URL:-${DELIVERY_DB_PATH:-sqlite:///$DEFAULT_DATA_HOME/delivery.db}}"
echo "Data: $DELIVERY_DATABASE_URL"

# Interview engine child - development parity with the packaged launcher.
# The interview flow talks to the engine over HTTP and nothing else starts it
# in development, so run-ui.sh owns it exactly like run-require.sh does.
ENGINE_PORT="${INTERVIEW_ENGINE_PORT:-8010}"
export INTERVIEW_ENGINE_URL="${INTERVIEW_ENGINE_URL:-http://127.0.0.1:$ENGINE_PORT}"
export INTERVIEW_ENGINE_TOKEN="${INTERVIEW_ENGINE_TOKEN:-local-interview-engine-token}"
ENGINE_DIR="${INTERVIEW_ENGINE_DIR:-$(cd "$ROOT/.." && pwd)/interview-engine}"
ENGINE_PID=""
engine_blocked=""
if command -v lsof >/dev/null 2>&1; then
  engine_listener=$(lsof -tiTCP:"$ENGINE_PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)
  if [ -n "$engine_listener" ]; then
    engine_cmd=$(ps -p "$engine_listener" -o command= 2>/dev/null || true)
    if [[ "$engine_cmd" == *"interview_engine.http_service"* ]]; then
      # A leftover engine keeps serving pre-pull code; replace it like the
      # packaged launcher does instead of silently reusing stale code.
      echo "Replacing orphaned interview engine $engine_listener..."
      kill -TERM "$engine_listener" 2>/dev/null || true
      for _ in $(seq 1 50); do kill -0 "$engine_listener" 2>/dev/null || break; sleep .1; done
      kill -0 "$engine_listener" 2>/dev/null && kill -KILL "$engine_listener" 2>/dev/null || true
    else
      engine_blocked=1
      echo "Warning: interview engine port $ENGINE_PORT is already used by an unrelated process (pid $engine_listener: $engine_cmd)."
      echo "Likely cause: another application claimed the engine port."
      echo "Next: stop that process or set INTERVIEW_ENGINE_PORT to a free port, then rerun scripts/run-ui.sh. Interviews cannot start until the engine is reachable."
    fi
  fi
fi
if [ -z "$engine_blocked" ]; then
  ENGINE_PYTHON=""
  for candidate in "${INTERVIEW_ENGINE_PYTHON:-}" "$ENGINE_DIR/.venv/bin/python"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import interview_engine,uvicorn,langchain_core' >/dev/null 2>&1; then ENGINE_PYTHON="$candidate"; break; fi
  done
  if [ -z "$ENGINE_PYTHON" ]; then
    echo "Warning: no Python environment with interview-engine, uvicorn and langchain_core was found."
    echo "Likely cause: interview-engine dependencies are not installed, or scripts/pull-all.sh updated the engine without syncing its environment."
    echo "Next: cd $ENGINE_DIR && .venv/bin/pip install -e . then rerun scripts/run-ui.sh. Interviews cannot start until the engine is running."
  else
    echo "Interview engine: http://127.0.0.1:$ENGINE_PORT (log: $DEFAULT_DATA_HOME/interview-engine.log)"
    ( cd "$ENGINE_DIR" && exec "$ENGINE_PYTHON" -m uvicorn interview_engine.http_service:app --host 127.0.0.1 --port "$ENGINE_PORT" ) > >(tee "$DEFAULT_DATA_HOME/interview-engine.log") 2>&1 &
    ENGINE_PID=$!
  fi
fi

echo "Stop: Ctrl-C"
"$PYTHON" -m delivery_api & DELIVERY_PID=$!
shutdown(){
  trap - EXIT INT TERM
  kill -TERM "$DELIVERY_PID" 2>/dev/null || true
  if [ -n "$ENGINE_PID" ]; then kill -TERM "$ENGINE_PID" 2>/dev/null || true; fi
  wait "$DELIVERY_PID" 2>/dev/null || true
  if [ -n "$ENGINE_PID" ]; then wait "$ENGINE_PID" 2>/dev/null || true; fi
}
trap shutdown EXIT INT TERM
wait "$DELIVERY_PID"
