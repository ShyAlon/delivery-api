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
