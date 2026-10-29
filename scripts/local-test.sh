#!/usr/bin/env bash
# Requi.re local manual testing, verified 2026-10-29.
# Usage: bash requi-local-test.sh /path/to/delivery-api [inspect|execute] [deepseek|ollama|both]
# Default: execute with both routes, matching the local manual-test workflow.
# /data and metrics persist in Docker volume requi-preview-data; Ctrl-C retains it.
# This script does NOT deploy, migrate, bootstrap a server or change .env/model defaults.
# inspect: Docker on this Mac, GitHub downloads, LIVE Supabase DB/auth. Buttons can write live data.
# execute: same live DB/auth; real admission/credits/billing. DeepSeek uses paid remote API.
# ollama: local Ollama, phi4 default, no paid model route; may load a heavy model.
# Execution provider choice does not authorize a paid run. Review cost/credits before Start.
set -euo pipefail
ROOT=${1:-$HOME/development/require/delivery-api}
MODE=${2:-execute}
PROVIDER=${3:-${PREVIEW_EXEC_PROVIDER:-both}}
fail(){ printf 'STOP: %s\n' "$*" >&2; exit 1; }
[ -d "$ROOT/.git" ] || fail "Not a delivery-api checkout: $ROOT"
cd "$ROOT"; ROOT=$PWD
for t in git docker; do command -v "$t" >/dev/null || fail "$t is required"; done
docker info >/dev/null 2>&1 || fail 'Start Docker Desktop, then retry.'
STORE=${PREVIEW_SETTINGS_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/requi/preview.env}
[ -r "$STORE" ] || fail "Missing saved DB/auth settings: $STORE. Use the existing preview-provision workflow; do not paste credentials in chat."
PERM=$(stat -c %a "$STORE" 2>/dev/null || stat -f %Lp "$STORE")
[ "$PERM" = 600 ] || fail "Settings must be mode600: chmod 600 '$STORE'"
for k in DELIVERY_DATABASE_URL DELIVERY_SUPABASE_URL DELIVERY_SUPABASE_ANON_KEY; do
 grep -q "^$k=." "$STORE" || fail "Missing $k in saved preview settings (value not printed)."
done
for r in delivery-api interview-engine spec-generator verification-loop; do
 D="$(dirname "$ROOT")/$r"
 [ -d "$D/.git" ] || fail "Missing sibling checkout: $D"
 [ "$(git -C "$D" branch --show-current)" = main ] || fail "$r must be on main; no branch is changed automatically."
 [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || fail "$r has tracked edits. Preserve them before running the preview."
done
printf '%s\n' 'This is a local Docker process using LIVE Supabase DB/auth, not a disposable test DB.'
printf '%s\n' 'No SSH by default. Network access to GitHub/package registries and Supabase is required.'
case "$MODE" in
 inspect)
  [ "${PREVIEW_VIA_SERVER:-0}" != 1 ] || fail 'Unset PREVIEW_VIA_SERVER; this wrapper is for the no-SSH local path.'
  printf '%s\n' 'Execution OFF; model calls disabled. Grant/Stop/Delete still write LIVE data.'
  printf '%s\n' 'Open http://127.0.0.1:8010 (unless PREVIEW_PORT overrides it). Ctrl-C stops the preview.'
  export PREVIEW_PORT=${PREVIEW_PORT:-8010}
  exec bash scripts/ops/preview.sh
  ;;
 execute)
  case "$PROVIDER" in deepseek|ollama|both) ;; *) fail 'Provider must be deepseek, ollama or both.';; esac
  export PREVIEW_EXEC_PROVIDER=$PROVIDER
  printf '%s\n' "Persistent local data volume: ${PREVIEW_DATA_VOLUME:-requi-preview-data} (retained on exit/rebuild)."
  printf '%s\n' 'Execution ON. Rebuild may replace the running preview; save Debug ZIP first.'
  printf '%s\n' 'Open http://127.0.0.1:8001 (unless PREVIEW_EXEC_PORT overrides it). Ctrl-C stops it.'
  if [ "$PROVIDER" = ollama ]; then
   printf '%s\n' 'Requires local Ollama at OLLAMA_HOST (default localhost:11434), model phi4 by default.'
   exec bash scripts/ops/preview-rebuild.sh
  fi
  printf '%s\n' 'Paid DeepSeek available. Task content goes to DeepSeek when you start that route.'
  # Read only the key as data, not shell code. Never print it or write it to disk.
  # Existing exported key wins; otherwise use the shared require/.env, not preview.env.
  PY="$ROOT/.venv/bin/python"
  [ -x "$PY" ] || fail 'Missing delivery .venv. Restore the existing ops Python3.11+ environment first.'
  "$PY" -c 'import sys,dotenv; assert sys.version_info >= (3,11)' >/dev/null 2>&1 || fail 'delivery .venv needs Python3.11+ and python-dotenv.'
  export REQUI_LOCAL_TEST_ROOT=$ROOT
  exec "$PY" - <<'PY'
import os, pathlib, subprocess, sys
from dotenv import dotenv_values
root=pathlib.Path(os.environ['REQUI_LOCAL_TEST_ROOT'])
env=dict(os.environ)
if not env.get('DEEPSEEK_API_KEY'):
    path=root.parent/'.env'
    if not path.is_file():
        sys.exit('STOP: no exported DEEPSEEK_API_KEY or shared require/.env. Keep keys out of chat.')
    key=dotenv_values(path).get('DEEPSEEK_API_KEY')
    if not key or len(key)<20 or any(c.isspace() for c in key):
        sys.exit('STOP: shared require/.env has no valid DEEPSEEK_API_KEY; value not printed.')
    env['DEEPSEEK_API_KEY']=key
raise SystemExit(subprocess.call(['bash','scripts/ops/preview-rebuild.sh'],cwd=root,env=env))
PY
  ;;
 *) fail 'Mode must be inspect or execute.';;
esac
