#!/usr/bin/env bash
# Usage: scripts/bench/db-cost-bisect.sh <commit> [<commit>...]
# Replays the hot endpoints on each commit (isolated worktree, disposable Postgres) and
# prints db_queries / db_ms / duration per request. Measurement only; touches no server.
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd); sibs=$(dirname "$repo")
out=${DB_COST_OUTPUT_DIR:-/tmp/db-cost-bisect}
mkdir -p "$out"
for sha in "$@"; do
  wt=$(mktemp -d /tmp/dbcost-XXXXXX)
  git -C "$repo" worktree add -q --detach "$wt" "$sha" || { echo "BISECT $sha checkout-failed"; continue; }
  cp "$repo/tests/bench_db_cost_replay.py" "$wt/tests/"
  cp "$repo/tests/strict_provider.py" "$wt/tests/"
  echo "BISECT $sha $(git -C "$repo" log -1 --format=%s "$sha" | cut -c1-70)"
  (cd "$wt" && PYTHONPATH="$wt/src:$wt/tests" DB_COST_SIBLINGS="$sibs" DB_COST_REPLAY=1 timeout "${DB_COST_TIMEOUT:-100}" python3 -m pytest tests/bench_db_cost_replay.py -q -s --tb=line -p no:cacheprovider 2>&1 | tee "$out/$sha.log" \
    | grep -E '^(DBCOST|DBTOP [0-9]+ )|^E |passed|failed' | cut -c1-240)
  result=$?
  echo "BISECT_EXIT $sha $result full_log=$out/$sha.log"
  git -C "$repo" worktree remove --force "$wt"
done
