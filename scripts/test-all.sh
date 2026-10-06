#!/usr/bin/env bash
# Run every Requi.re repo's unit and browser tests from one place and print one table.
#
#   scripts/test-all.sh [--repo NAME] [--fast]
#
# Mirrors CI: each repo's tests run with the same command its scripts/test-local.sh
# ends with (`pytest -q` in the repo's .venv). Setup (venv, installs, Chromium) stays
# in each repo's own scripts/test-local.sh, which is the only step that needs the
# network. Tests run with the network blocked where the OS allows it (Linux).
# Never touches git. Never runs live-model or real-model tests.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOS_ROOT="${REQUI_RE_REPOS_ROOT:-$(cd "$PROJECT/.." && pwd)}"
REPOS="vmodel-contracts spec-generator interview-engine delivery-api"
ONLY="" FAST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) ONLY="${2:-}"; shift 2 ;;
    --fast) FAST=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Error: unknown option '$1'. Use --repo NAME or --fast." >&2; exit 2 ;;
  esac
done
if [ -n "$ONLY" ] && ! printf '%s\n' $REPOS | grep -qx "$ONLY"; then
  echo "Error: unknown repo '$ONLY'. Choose one of: $REPOS" >&2; exit 2
fi

LOGDIR="$PROJECT/.test-all-logs/$(date +%Y%m%d-%H%M%S)"
RAN=0  # the log directory is created only when a suite actually runs

# Can this machine run a command with the network switched off?
if unshare -rn true >/dev/null 2>&1; then NETWORK="blocked"; else NETWORK="not enforced"; fi

ROWS=() FINDINGS=() FAILED_REPOS=0 BAD=0
add_row() { ROWS+=("$(printf '%-18s %-12s %7s %7s %7s %9s  %s' "$@")"); }

for repo in $REPOS; do
  [ -n "$ONLY" ] && [ "$repo" != "$ONLY" ] && continue
  path="$REPOS_ROOT/$repo"
  if [ ! -d "$path" ]; then
    add_row "$repo" "missing" - - - - "checkout not found at $path"
    FINDINGS+=("$repo: checkout not found at $path"); BAD=1; continue
  fi
  if [ ! -f "$path/pyproject.toml" ] || [ ! -d "$path/tests" ]; then
    add_row "$repo" "no tests" - - - - "no tests wired"
    FINDINGS+=("$repo: no pyproject.toml/tests directory, so nothing ran (not a pass)"); BAD=1; continue
  fi
  py="$path/.venv/bin/python"
  if [ ! -x "$py" ]; then
    add_row "$repo" "not set up" - - - - "run $repo/scripts/test-local.sh once"
    FINDINGS+=("$repo: no .venv, so nothing ran. Set up once with $repo/scripts/test-local.sh (needs network)"); BAD=1; continue
  fi
  # A .venv that cannot import pytest would otherwise show up as an unexplained NO RESULT.
  probe=$("$py" -c 'import pytest' 2>&1) || {
    reason="pytest not installed: run $repo/scripts/test-local.sh"
    if ! printf '%s' "$probe" | grep -q 'No module named'; then reason="$repo's .venv python does not run: $(printf '%s' "$probe" | tail -1 | cut -c1-120)"; fi
    add_row "$repo" "not ready" - - - - "$reason"
    FINDINGS+=("$repo: $reason (nothing ran)"); BAD=1; continue
  }
  args=(-q -rf -p no:cacheprovider)
  if [ "$FAST" = 1 ]; then args+=(--ignore-glob='*browser*' --ignore-glob='*playwright*'); fi
  mkdir -p "$LOGDIR"; RAN=1
  log="$LOGDIR/$repo.log"
  start=$(date +%s)
  if [ "$NETWORK" = "blocked" ]; then
    # Loopback stays up: the browser tests start their own local server.
    (cd "$path" && unshare -rn sh -c 'ip link set lo up 2>/dev/null; exec "$@"' sh "$py" -m pytest "${args[@]}") >"$log" 2>&1
  else
    (cd "$path" && "$py" -m pytest "${args[@]}") >"$log" 2>&1
  fi
  code=$?
  secs=$(( $(date +%s) - start ))
  summary=$(grep -E '[0-9]+ (passed|failed|error|skipped)' "$log" | tail -1)
  count() { echo "$summary" | grep -oE "[0-9]+ $1" | grep -oE '^[0-9]+' || echo 0; }
  p=$(count passed); f=$(( $(count failed) + $(count error) )); s=$(count skipped)
  if [ -z "$summary" ]; then
    add_row "$repo" "NO RESULT" - - - "${secs}s" "pytest produced no summary; see $log"
    FINDINGS+=("$repo: pytest produced no result summary (exit $code); see $log")
    while IFS= read -r line; do FINDINGS+=("    | $(printf '%s' "$line" | cut -c1-160)"); done < <(tail -n 5 "$log")
    BAD=1; continue
  fi
  state="ok"; [ "$f" -gt 0 ] && state="FAILING"
  add_row "$repo" "$state" "$p" "$f" "$s" "${secs}s" "network: $NETWORK"
  if [ "$f" -gt 0 ]; then
    FAILED_REPOS=$((FAILED_REPOS + 1))
    while IFS= read -r line; do FINDINGS+=("$repo: ${line#FAILED }"); done < <(grep -E '^(FAILED|ERROR) ' "$log" | cut -c1-200)
  fi
done

printf '\n%-18s %-12s %7s %7s %7s %9s  %s\n' repo result passed failed skipped duration notes
for row in "${ROWS[@]}"; do echo "$row"; done
if [ ${#FINDINGS[@]} -gt 0 ]; then
  printf '\nFindings:\n'
  for line in "${FINDINGS[@]}"; do echo "  - $line"; done
fi
printf '\nNetwork during tests: %s\n' "$NETWORK"
if [ "$RAN" = 1 ]; then printf 'Logs: %s\n' "$LOGDIR"; else echo "Logs: none (no suite ran)"; fi
if [ "$FAILED_REPOS" -eq 0 ] && [ "$BAD" -eq 0 ]; then
  echo "VERDICT: ALL GREEN"; exit 0
fi
echo "VERDICT: $((FAILED_REPOS + BAD)) REPO(S) NEED ATTENTION"; exit 1
