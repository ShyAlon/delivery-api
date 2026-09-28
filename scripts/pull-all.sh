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

# Cross-repo contracts are pinned in each consumer's project metadata. The
# source pull alone leaves previously installed wheels stale and can make both
# HTTP services fail to import. Sync only the two services pulled above.
for repo in interview-engine delivery-api; do
  path="$REPOS_ROOT/$repo"
  if [ -x "$path/.venv/bin/python" ]; then
    printf 'syncing %-15s ' "$repo"
    if command -v uv >/dev/null 2>&1; then
      (cd "$path" && uv sync --quiet) || { echo 'failed' >&2; exit 1; }
    else
      "$path/.venv/bin/python" -m pip install -e "$path" || { echo 'failed' >&2; exit 1; }
    fi
    echo 'ok'
  else
    echo "Notice: $repo has no .venv; scripts/run-ui.sh will report missing dependencies."
  fi
done
