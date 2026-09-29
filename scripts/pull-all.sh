#!/usr/bin/env bash
# Consciously update the six sibling Requi.re checkouts without merges.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOS_ROOT="${REQUI_RE_REPOS_ROOT:-$(cd "$PROJECT/.." && pwd)}"
REPOS="delivery-api interview-engine spec-generator vmodel-contracts verification-loop vmodel-spec-app"
for repo in $REPOS; do
  path="$REPOS_ROOT/$repo"
  [ -d "$path/.git" ] || { echo "Error: required repository '$repo' is missing at $path." >&2; exit 1; }
done
for repo in $REPOS; do
  printf '%-20s ' "$repo"
  git -C "$REPOS_ROOT/$repo" pull --ff-only
done

# Sync every Python checkout, not just the two HTTP services. A source pull
# can leave an existing venv with old pinned contracts or missing transitive
# imports (for example spec-generator's langgraph). `--locked` refuses to
# rewrite a lockfile on the user's machine; a failure names the exact repo.
for repo in $REPOS; do
  path="$REPOS_ROOT/$repo"
  if [ "$repo" = "vmodel-contracts" ]; then
    echo "Notice: vmodel-contracts is the source package; no separate runtime environment to sync."
    continue
  fi
  [ -f "$path/pyproject.toml" ] || { echo "Notice: $repo has no Python project metadata; skipping environment sync."; continue; }
  printf 'syncing %-15s ' "$repo"
  # uv may keep a previously installed Git package when its commit changes but
  # its project version does not. Force the pinned contracts wheel into each
  # dependent runtime. --locked still prevents rewriting the user's lockfile.
  if grep -q 'vmodel-contracts @ git+' "$path/pyproject.toml"; then
    contracts_reinstall=(--reinstall-package vmodel-contracts)
  else
    contracts_reinstall=()
  fi
  if command -v uv >/dev/null 2>&1 && [ -f "$path/uv.lock" ]; then
    (cd "$path" && uv sync --locked --quiet "${contracts_reinstall[@]}") || { echo "Error: $repo environment sync failed. Check its lockfile and dependencies." >&2; exit 1; }
  elif command -v uv >/dev/null 2>&1; then
    (cd "$path" && uv sync --quiet "${contracts_reinstall[@]}") || { echo "Error: $repo environment sync failed." >&2; exit 1; }
  elif [ -x "$path/.venv/bin/python" ]; then
    # pip's VCS resolver also keys off the declared version. Reinstall the
    # pinned dependency before refreshing the editable sibling project.
    if [ "${#contracts_reinstall[@]}" -gt 0 ]; then
      "$path/.venv/bin/python" -m pip install --force-reinstall --no-deps "$(grep -o 'vmodel-contracts @ git+[^" ]*' "$path/pyproject.toml" | head -1)" || { echo "Error: $repo pinned contracts install failed." >&2; exit 1; }
    fi
    "$path/.venv/bin/python" -m pip install -e "$path" || { echo "Error: $repo environment install failed." >&2; exit 1; }
    "$path/.venv/bin/python" -m pip check || { echo "Error: $repo environment has unsatisfied package dependencies." >&2; exit 1; }
  else
    echo "Error: $repo has no .venv and uv is unavailable." >&2
    echo "Next: install uv or create $repo/.venv with python3 -m venv, then rerun pull-all.sh." >&2
    exit 1
  fi
  echo 'ok'
done
