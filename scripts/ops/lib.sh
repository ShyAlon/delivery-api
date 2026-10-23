#!/usr/bin/env bash
# Shared start for the operations scripts. Source it, then call: ops_start <name> "$0" [--venv]
# It brings this checkout to origin/main by itself, re-runs the script if the pull changed it,
# prints the ops identity, and refuses to go on if anything under scripts/ops differs from origin/main.
OPS_NAME=""; OPS_SELF=""; OPS_NOTE="Nothing was changed on the server."
abort(){ echo; echo "ABORTED [$1]: $2"; echo "${3:-$OPS_NOTE}"; echo "Fix: ${4:-run scripts/ops/$OPS_NAME.sh again; if it repeats, send me this whole output}"; echo "Then run: scripts/ops/$OPS_NAME.sh"; exit 1; }
ops_venv(){
  [ -x .venv/bin/python ] && return 0
  echo "== building .venv (first run only)"
  local c="" k
  for k in python3.12 python3.13 python3.14 python3; do
    if command -v "$k" >/dev/null 2>&1 && "$k" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then c=$k; break; fi
  done
  [ -n "$c" ] || abort NO-PYTHON "no Python 3.12 or newer on PATH" "" "brew install python@3.12"
  "$c" -m venv .venv || abort VENV "cannot create .venv" "" "rm -rf $PWD/.venv"
  .venv/bin/python -m pip install -q vendor/vmodel_contracts-0.28.0-py3-none-any.whl && .venv/bin/python -m pip install -q -e . || { rm -rf .venv; abort VENV-INSTALL "pip could not install the project" "The half-built .venv was removed." "check your network, then run it again"; }
}
ops_start(){
  OPS_NAME=$1; OPS_SELF=$2; local want_venv=${3:-}
  local repo; repo=$(cd "$(dirname "$OPS_SELF")/../.." 2>/dev/null && pwd) && git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || abort NO-REPO "$OPS_SELF is not inside a git checkout" "Nothing changed." "git clone https://github.com/ShyAlon/delivery-api ~/development/require/delivery-api   (then run its scripts/ops/$OPS_NAME.sh)"
  cd "$repo"
  git remote get-url origin | grep -qi 'ShyAlon/delivery-api' || abort REMOTE "origin is not ShyAlon/delivery-api" "" "git -C $PWD remote -v   (this must be your delivery-api checkout)"
  git fetch -q origin main || abort FETCH "cannot fetch origin main" "" "git -C $PWD fetch origin main   (look at the error)"
  local cur; cur=$(git rev-parse --abbrev-ref HEAD)
  if [ "$cur" != main ]; then
    [ "$(git rev-list --count origin/main..HEAD)" = 0 ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || abort BRANCH "you are on $cur with work of your own" "" "git -C $PWD status --short   (commit or stash, then: git checkout main)"
    echo "== switching to main"; git checkout -q main || abort CHECKOUT "cannot switch to main" "" "git -C $PWD branch -a"
  fi
  [ -z "$(git status --porcelain --untracked-files=no)" ] || abort DIRTY "tracked files have uncommitted changes" "" "git -C $PWD status --short   (commit, stash or discard them)"
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    git pull -q --ff-only origin main || abort PULL "local main cannot fast-forward to origin/main" "" "git -C $PWD log --oneline origin/main..main   (local commits on main?)"
    if [ -z "${OPS_REEXEC:-}" ]; then echo "== updated to origin/main, restarting this script"; export OPS_REEXEC=1; exec bash "$repo/scripts/ops/$OPS_NAME.sh"; fi
  fi
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || abort STALE "this checkout is not at origin/main after pulling" "" "git -C $PWD status"
  git diff --quiet origin/main -- scripts/ops || abort OPS-DIFFERS "scripts/ops differs from origin/main" "" "git -C $PWD diff origin/main -- scripts/ops   (discard your edits)"
  echo "ops $(git rev-parse --short=8 HEAD)  ($OPS_NAME, origin/main)"
  if ! command -v ops >/dev/null 2>&1 && [ -t 0 ] && [ -z "${OPS_REEXEC:-}" ]; then
    printf 'ops is not on your PATH yet. Type yes to put it there (afterwards you only type: ops %s), or press Enter to skip: ' "$OPS_NAME"; read -r _a
    [ "$_a" = yes ] && { bash "$repo/scripts/ops/ops" install --yes || echo "(not installed; the script goes on)"; }
  fi
  [ "$want_venv" = --venv ] && ops_venv
  return 0
}
