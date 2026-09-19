#!/usr/bin/env bash
# Build one Requi.re runtime: one Python, all executable packages, one launcher.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOS_ROOT="${REQUI_RE_REPOS_ROOT:-$(cd "$PROJECT/.." && pwd)}"
OUTPUT="${REQUI_RE_DIST_DIR:-$PROJECT/dist/requi-re}"
BOOTSTRAP_PYTHON="${REQUI_RE_BUILD_PYTHON:-python3}"
PACKAGES="delivery-api interview-engine spec-generator vmodel-contracts verification-loop"
for repo in $PACKAGES vmodel-spec-app; do
  [ -d "$REPOS_ROOT/$repo" ] || { echo "Error: required repository '$repo' is missing."; echo "Likely cause: the Requi.re checkout is incomplete under $REPOS_ROOT."; echo "Next: clone $repo into $REPOS_ROOT, then rerun scripts/build-dist.sh."; exit 1; }
done
command -v "$BOOTSTRAP_PYTHON" >/dev/null 2>&1 || { echo "Error: build Python '$BOOTSTRAP_PYTHON' was not found."; echo "Likely cause: Python 3.10+ is not on PATH."; echo "Next: set REQUI_RE_BUILD_PYTHON=/path/to/python and retry."; exit 1; }
rm -rf "$OUTPUT"; mkdir -p "$OUTPUT"
"$BOOTSTRAP_PYTHON" -m venv "$OUTPUT/runtime"
PYTHON="$OUTPUT/runtime/bin/python"
"$PYTHON" -m pip install --disable-pip-version-check --upgrade pip
# One resolver transaction means local sibling packages, not runtime discovery,
# own the executable dependency graph.
# Install runtime dependencies once, then install all local wheels without
# letting historical direct-URL pins replace the checked-out contracts build.
"$PYTHON" -m pip install fastapi uvicorn pydantic httpx python-multipart \
  langgraph langgraph-checkpoint-sqlite langchain-core langchain-openai langchain-ollama
"$PYTHON" -m pip install --no-deps \
  "$REPOS_ROOT/vmodel-contracts" "$REPOS_ROOT/interview-engine" \
  "$REPOS_ROOT/spec-generator" "$REPOS_ROOT/verification-loop" "$PROJECT"
mkdir -p "$OUTPUT/support"
cp -R "$REPOS_ROOT/vmodel-spec-app/scripts" "$OUTPUT/support/vmodel-spec-app-scripts"
cat > "$OUTPUT/run-require.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/runtime/bin/python"
[ -x "$PYTHON" ] || { echo "Error: Requi.re runtime is incomplete."; echo "Likely cause: this launcher was separated from its built runtime."; echo "Next: copy the whole dist/requi-re folder and run this launcher from inside it."; exit 1; }
"$PYTHON" -c 'import delivery_api, interview_engine, spec_generator, verification_loop, langchain_core' >/dev/null 2>&1 || { echo "Error: Requi.re runtime validation failed."; echo "Likely cause: the built folder is incomplete or was copied across incompatible operating systems."; echo "Next: rebuild on the target operating system with scripts/build-dist.sh."; exit 1; }
export REQUI_RE_DIST=1
export INTERVIEW_ENGINE_PYTHON="$PYTHON"
export DELIVERY_BASIC_AUTH_ENABLED="${DELIVERY_BASIC_AUTH_ENABLED:-1}"
export DELIVERY_COOKIE_SECURE="${DELIVERY_COOKIE_SECURE:-0}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/requi-re"
mkdir -p "$DATA_HOME"
export DELIVERY_DATABASE_URL="${DELIVERY_DATABASE_URL:-${DELIVERY_DB_PATH:-sqlite:///$DATA_HOME/delivery.db}}"
echo "Data: $DELIVERY_DATABASE_URL"
cd "$ROOT"
exec "$PYTHON" -m delivery_api
EOF
chmod +x "$OUTPUT/run-require.sh"
{
  printf 'Requi.re distribution manifest\nBuilt: '; date -u '+%Y-%m-%dT%H:%M:%SZ'
  printf 'Build Python: '; "$PYTHON" --version
  printf 'Platform: '; "$PYTHON" -c 'import platform; print(platform.platform())'
  printf '\nRepositories:\n'
  for repo in $PACKAGES vmodel-spec-app; do
    sha=$(git -C "$REPOS_ROOT/$repo" rev-parse HEAD)
    dirty=$(test -n "$(git -C "$REPOS_ROOT/$repo" status --porcelain)" && printf ' dirty' || true)
    printf '%-20s %s%s\n' "$repo" "$sha" "$dirty"
  done
  printf '\nInstalled packages:\n'; "$PYTHON" -m pip freeze --all
} > "$OUTPUT/MANIFEST.txt"
printf '\nBuilt Requi.re at %s\nLaunch: %s/run-require.sh\nManifest: %s/MANIFEST.txt\n' "$OUTPUT" "$OUTPUT" "$OUTPUT"
