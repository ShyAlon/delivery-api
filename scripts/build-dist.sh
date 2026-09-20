#!/usr/bin/env bash
# Incrementally build one Requi.re runtime: one Python, all packages, one launcher.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOS_ROOT="${REQUI_RE_REPOS_ROOT:-$(cd "$PROJECT/.." && pwd)}"; OUTPUT="${REQUI_RE_DIST_DIR:-$PROJECT/dist/requi-re}"
BOOTSTRAP_PYTHON="${REQUI_RE_BUILD_PYTHON:-python3}"; PACKAGES="delivery-api interview-engine spec-generator vmodel-contracts verification-loop"
CLEAN=${REQUI_RE_CLEAN:-0}; [ "${1:-}" = "--clean" ] && CLEAN=1
[ $# -le 1 ] || { echo "Usage: scripts/build-dist.sh [--clean]"; exit 2; }
for repo in $PACKAGES vmodel-spec-app; do [ -d "$REPOS_ROOT/$repo/.git" ] || { echo "Error: required repository '$repo' is missing."; echo "Likely cause: the Requi.re checkout is incomplete under $REPOS_ROOT."; echo "Next: clone $repo into $REPOS_ROOT, then retry."; exit 1; }; done
echo "Resolved Requi.re source revisions:"
for repo in $PACKAGES vmodel-spec-app; do
  printf '  %-20s %s  %s\n' "$repo" "$(git -C "$REPOS_ROOT/$repo" rev-parse --short=12 HEAD)" "$REPOS_ROOT/$repo"
  if git -C "$REPOS_ROOT/$repo" remote get-url origin >/dev/null 2>&1; then
    git -C "$REPOS_ROOT/$repo" fetch --quiet origin 2>/dev/null || true
    upstream=$(git -C "$REPOS_ROOT/$repo" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
    if [ -n "$upstream" ]; then
      behind=$(git -C "$REPOS_ROOT/$repo" rev-list --count "HEAD..$upstream" 2>/dev/null || echo 0)
      if [ "$behind" -gt 0 ]; then
        echo "WARNING: $repo is $behind commit(s) behind $upstream. This build would mix repository versions."
        echo "Next: git -C '$REPOS_ROOT/$repo' pull --ff-only, then rerun this build."
        stale_repos="${stale_repos:-} $repo"
      fi
    fi
  fi
done
if [ -n "${stale_repos:-}" ]; then
  echo "Error: Requi.re source checkouts are not all current."
  echo "Likely cause: only one sibling repository was pulled:${stale_repos}."
  echo "Next: pull every listed checkout, then rerun scripts/build-dist.sh."
  exit 1
fi
command -v "$BOOTSTRAP_PYTHON" >/dev/null || { echo "Error: build Python '$BOOTSTRAP_PYTHON' was not found."; echo "Likely cause: Python 3.10+ is not on PATH."; echo "Next: set REQUI_RE_BUILD_PYTHON=/path/to/python and retry."; exit 1; }
PARENT=$(dirname "$OUTPUT"); mkdir -p "$PARENT"; STAGE="$PARENT/.requi-re-stage-$$"; BACKUP="$PARENT/.requi-re-previous-$$"
cleanup(){ rm -rf "$STAGE"; }; trap cleanup EXIT
if [ "$CLEAN" != 1 ] && [ -x "$OUTPUT/runtime/bin/python" ]; then cp -a "$OUTPUT" "$STAGE"; else mkdir -p "$STAGE"; fi
STATE="$STAGE/.build-state"; OLD_STATE="$OUTPUT/.build-state"
state_get(){ [ -f "$OLD_STATE" ] && awk -F= -v key="$1" '$1==key{print substr($0,index($0,"=")+1);exit}' "$OLD_STATE" || true; }
repo_fingerprint(){ local repo=$1 status diffhash; status=$(git -C "$REPOS_ROOT/$repo" status --porcelain); diffhash=clean; [ -n "$status" ] && diffhash=$(git -C "$REPOS_ROOT/$repo" diff --binary HEAD | shasum -a 256 | awk '{print $1}'); printf '%s:%s' "$(git -C "$REPOS_ROOT/$repo" rev-parse HEAD)" "$diffhash"; }
DEPS='fastapi uvicorn pydantic httpx python-multipart langgraph langgraph-checkpoint-sqlite langchain-core langchain-openai langchain-ollama python-dotenv'
WITH_TURSO=${REQUI_RE_WITH_TURSO:-0}; [ "$WITH_TURSO" = 1 ] && DEPS="$DEPS libsql"
DEP_INPUT="$DEPS|$($BOOTSTRAP_PYTHON --version 2>&1)"
for repo in $PACKAGES; do DEP_INPUT="$DEP_INPUT|$(shasum -a 256 "$REPOS_ROOT/$repo/pyproject.toml" | awk '{print $1}')"; done
DEP_HASH=$(printf '%s' "$DEP_INPUT" | shasum -a 256 | awk '{print $1}')
PYTHON="$STAGE/runtime/bin/python"
BUILD_LOG="$PARENT/requi-re-build.log"
install_dependencies(){
  local wheel_dir="$STAGE/.wheel-probe"; rm -rf "$wheel_dir"; mkdir -p "$wheel_dir"
  if [ "$WITH_TURSO" = 1 ] && ! "$PYTHON" -m pip download --only-binary=:all: --no-deps libsql -d "$wheel_dir" >/dev/null 2>&1; then
    missing=""; command -v cmake >/dev/null 2>&1 || missing="cmake"; command -v cargo >/dev/null 2>&1 || missing="${missing:+$missing and }rust"
    if [ -n "$missing" ]; then
      echo "Error: libSQL needs a source build on this Python/platform, but $missing is missing."
      echo "Likely cause: PyPI has no prebuilt libsql wheel for $($PYTHON -c 'import sys,platform; print(f"Python {sys.version_info.major}.{sys.version_info.minor} {platform.system()} {platform.machine()}")')."
      echo "Next: run: brew install cmake rust, then rerun scripts/build-dist.sh."
      return 1
    fi
    echo "boundary=dependency package=libsql decision=source-build reason=no-compatible-wheel"
  elif [ "$WITH_TURSO" = 1 ]; then
    echo "boundary=dependency package=libsql decision=wheel reason=compatible-wheel-found"
  else
    echo "boundary=dependency package=libsql decision=skip reason=optional-turso-disabled"
  fi
  if ! "$PYTHON" -m pip install $DEPS >"$BUILD_LOG" 2>&1; then
    echo "Error: Requi.re dependencies could not be installed."
    if grep -qi 'cmake.*not installed\|No such file.*cmake' "$BUILD_LOG"; then echo "Likely cause: libSQL needs cmake for a source build on this Python/platform."; echo "Next: run: brew install cmake, then retry. Raw build output: $BUILD_LOG"
    elif grep -qi 'cargo.*not found\|rust.*not found' "$BUILD_LOG"; then echo "Likely cause: libSQL needs the Rust toolchain for a source build on this Python/platform."; echo "Next: run: brew install rust, then retry. Raw build output: $BUILD_LOG"
    else echo "Likely cause: pip or a native dependency build failed."; echo "Next: inspect $BUILD_LOG, fix the named dependency, then retry."; fi
    return 1
  fi
}
if [ "$CLEAN" = 1 ] || [ ! -x "$PYTHON" ]; then
  rm -rf "$STAGE/runtime"; "$BOOTSTRAP_PYTHON" -m venv "$STAGE/runtime"
  "$PYTHON" -m pip install --disable-pip-version-check --upgrade pip
  echo "boundary=dependencies decision=install reason=clean-or-missing-runtime"
  install_dependencies
elif [ "$(state_get dependencies)" != "$DEP_HASH" ]; then
  echo "boundary=dependencies decision=install reason=dependency-input-changed"; install_dependencies
else echo "boundary=dependencies decision=skip reason=unchanged"; fi
: > "$STATE"; printf 'dependencies=%s\n' "$DEP_HASH" >> "$STATE"
for repo in $PACKAGES; do
  current=$(repo_fingerprint "$repo"); prior=$(state_get "repo.$repo")
  if [ "$CLEAN" = 1 ] || [ ! -x "$OUTPUT/runtime/bin/python" ] || [ "$current" != "$prior" ]; then
    echo "boundary=package package=$repo decision=install reason=$([ "$CLEAN" = 1 ] && echo clean-build || echo source-changed)"
    "$PYTHON" -m pip install --no-deps "$REPOS_ROOT/$repo"
  else echo "boundary=package package=$repo decision=skip reason=unchanged"; fi
  printf 'repo.%s=%s\n' "$repo" "$current" >> "$STATE"
done
support_fp=$(repo_fingerprint vmodel-spec-app); if [ "$CLEAN" = 1 ] || [ "$support_fp" != "$(state_get repo.vmodel-spec-app)" ]; then rm -rf "$STAGE/support/vmodel-spec-app-scripts"; mkdir -p "$STAGE/support"; cp -R "$REPOS_ROOT/vmodel-spec-app/scripts" "$STAGE/support/vmodel-spec-app-scripts"; echo 'boundary=package package=vmodel-spec-app decision=copy reason=source-changed'; else echo 'boundary=package package=vmodel-spec-app decision=skip reason=unchanged'; fi
printf 'repo.vmodel-spec-app=%s\n' "$support_fp" >> "$STATE"
cat > "$STAGE/run-require.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; PYTHON="$ROOT/runtime/bin/python"
[ -x "$PYTHON" ] || { echo "Error: Requi.re runtime is incomplete."; echo "Likely cause: this launcher was separated from its built runtime."; echo "Next: copy the whole dist/requi-re folder and retry."; exit 1; }
"$PYTHON" -c 'import delivery_api, interview_engine, spec_generator, verification_loop, langchain_core' >/dev/null 2>&1 || { echo "Error: Requi.re runtime validation failed."; echo "Likely cause: the built folder is incomplete or incompatible."; echo "Next: rebuild on this operating system with scripts/build-dist.sh --clean."; exit 1; }
export REQUI_RE_DIST=1 INTERVIEW_ENGINE_PYTHON="$PYTHON" DELIVERY_BASIC_AUTH_ENABLED="${DELIVERY_BASIC_AUTH_ENABLED:-1}" DELIVERY_COOKIE_SECURE="${DELIVERY_COOKIE_SECURE:-0}" OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/requi-re"; mkdir -p "$DATA_HOME"; export DELIVERY_DATABASE_URL="${DELIVERY_DATABASE_URL:-${DELIVERY_DB_PATH:-sqlite:///$DATA_HOME/delivery.db}}"
PID_FILE="$DATA_HOME/server.pid"
# Upgrade path for servers launched before PID ownership existed: discover the
# listener once, validate it is delivery_api, and adopt the same replacement flow.
if [ ! -f "$PID_FILE" ] && command -v lsof >/dev/null 2>&1; then
  listener_pid=$(lsof -tiTCP:"${PORT:-8000}" -sTCP:LISTEN 2>/dev/null | head -1 || true)
  if [ -n "$listener_pid" ]; then
    listener_cmd=$(ps -p "$listener_pid" -o command= 2>/dev/null || true)
    if [[ "$listener_cmd" == *"delivery_api"* ]]; then echo "$listener_pid" > "$PID_FILE"
    else
      echo "Error: port ${PORT:-8000} is already used by PID $listener_pid."
      echo "Likely cause: another service is listening where Requi.re starts."
      echo "Next: stop that service or set PORT to another port, then retry."; exit 1
    fi
  fi
fi
if [ -f "$PID_FILE" ]; then
  old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    old_cmd=$(ps -p "$old_pid" -o command= 2>/dev/null || true)
    if [[ "$old_cmd" == *"delivery_api"* ]]; then
      echo "Replacing running Requi.re process $old_pid..."; kill -TERM "$old_pid"
      for _ in $(seq 1 100); do kill -0 "$old_pid" 2>/dev/null || break; sleep .1; done
      kill -0 "$old_pid" 2>/dev/null && { echo "Error: running Requi.re process $old_pid did not stop."; echo "Likely cause: it ignored the graceful shutdown signal."; echo "Next: stop PID $old_pid manually, then rerun this launcher."; exit 1; }
    else
      echo "Error: saved Requi.re PID $old_pid belongs to a different process."
      echo "Likely cause: the old PID file outlived its process and the OS reused that PID."
      echo "Next: remove $PID_FILE after checking PID $old_pid, then retry."; exit 1
    fi
  fi
fi
echo $$ > "$PID_FILE"; trap 'rm -f "$PID_FILE"' EXIT INT TERM
echo "Data: $DELIVERY_DATABASE_URL"
echo "Installed Requi.re source revisions:"
sed -n '/^Repositories:$/,/^Installed packages:$/p' "$ROOT/MANIFEST.txt" 2>/dev/null | sed '1d;$d' || true
cd "$ROOT"; exec "$PYTHON" -m delivery_api
EOF
chmod +x "$STAGE/run-require.sh"
"$PYTHON" -c 'import delivery_api, interview_engine, spec_generator, verification_loop, langchain_core'
{
 printf 'Requi.re distribution manifest\nBuilt: '; date -u '+%Y-%m-%dT%H:%M:%SZ'; printf 'Build Python: '; "$PYTHON" --version; printf 'Platform: '; "$PYTHON" -c 'import platform; print(platform.platform())'; printf '\nRepositories:\n'
 for repo in $PACKAGES vmodel-spec-app; do printf '%-20s %s\n' "$repo" "$(repo_fingerprint "$repo")"; done
 printf '\nInstalled packages:\n'; "$PYTHON" -m pip freeze --all
} > "$STAGE/MANIFEST.txt"
# Atomic promotion: a failed build never reaches this point, so OUTPUT remains runnable.
[ -e "$OUTPUT" ] && mv "$OUTPUT" "$BACKUP"; if mv "$STAGE" "$OUTPUT"; then rm -rf "$BACKUP"; else [ -e "$BACKUP" ] && mv "$BACKUP" "$OUTPUT"; exit 1; fi
trap - EXIT
printf '\nBuilt Requi.re at %s\nLaunch: %s/run-require.sh\nManifest: %s/MANIFEST.txt\n' "$OUTPUT" "$OUTPUT" "$OUTPUT"
