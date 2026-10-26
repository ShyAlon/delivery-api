#!/usr/bin/env bash
# Rebuild the local execution preview from the newest origin/main code and launch it. One command, no arguments.
# What it does, in order: stops and removes the old preview container if one is left over, fast-forwards the four
# checkouts (delivery-api, interview-engine, spec-generator, verification-loop) to origin/main, removes the old
# requi-full image tags, then hands over to ops preview-exec, which builds the candidate image (publishing its tag
# only after the smoke check passes) and launches it with its own gates. When the newest code is already built,
# the build is skipped: preview-exec smoke-checks the existing tag and reuses it. A running preview is stopped
# only after a typed yes, because that ends its console and its in-memory log. Ctrl-C rules of preview-exec apply.
set -euo pipefail
NAME=requi-preview-exec
. "$(dirname "$0")/lib.sh"; ops_start preview-rebuild "$0"
REPO=$PWD

for t in docker git; do command -v $t >/dev/null 2>&1 || abort "TOOL-$t" "$t is missing" "" "install $t (Docker: open Docker Desktop once, or brew install --cask docker)"; done
docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"

# 1. the old container goes away here, so preview-exec's LEFTOVER guard never fires. A RUNNING one is stopped
# only after a typed yes: that ends its console and its in-memory log (the Debug ZIP lives only in there).
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "== a preview is RUNNING now. Stopping it ends its console and its in-memory log"
    echo "   (download the Debug ZIP first if you still need it)."
    printf 'Type yes (English keyboard) to stop and replace it, anything else changes nothing: '
    read -r A < "${PREVIEW_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "" "run ops preview-rebuild again when the running preview can be stopped"
  fi
  docker rm -f "$NAME" >/dev/null || abort RM "the old container $NAME could not be removed" "" "docker rm -f $NAME"
  echo "== old container $NAME removed"
fi

# 2. the four checkouts at origin/main (the same rules preview-exec enforces, with the same fix lines)
ROOT=${REQUI_REPOS_ROOT:-$(dirname "$REPO")}; TAG=""
for r in delivery-api interview-engine spec-generator verification-loop; do
  D=$ROOT/$r
  git -C "$D" rev-parse --is-inside-work-tree >/dev/null 2>&1 || abort "NO-$r" "no checkout at $D" "" "git clone https://github.com/ShyAlon/$r $D"
  git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "" "git -C $D fetch origin main"
  [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes to tracked files" "" "git -C $D status --short   (commit, stash or discard them)"
  [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" = main ] || abort "BRANCH-$r" "$r is not on main" "" "git -C $D checkout main"
  git -C "$D" pull -q --ff-only origin main || abort "PULL-$r" "$r main cannot fast-forward to origin/main" "" "git -C $D log --oneline origin/main..main"
  [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r is not at origin/main after pulling" "" "git -C $D status"
  S=$(git -C "$D" rev-parse --short=8 HEAD); TAG="${TAG:+$TAG-}$S"
done
IMAGE=requi-full:$TAG
echo "== all four checkouts are at origin/main; candidate image $IMAGE"

# 3. old image tags go away. The candidate tag is kept: the same code builds the same image, and preview-exec
# smoke-checks that tag before reusing it, so keeping it is the fast path when the code did not change.
REMOVED=0; KEPT=""
while IFS= read -r img; do
  [ -n "$img" ] || continue
  case "$img" in *:'<none>') continue;; esac
  if [ "$img" = "$IMAGE" ]; then KEPT=$img; continue; fi
  if docker rmi "$img" >/dev/null 2>&1; then REMOVED=$((REMOVED+1)); else echo "== kept $img (could not remove it; it is not in the way)"; fi
done <<EOF
$(docker images requi-full --format '{{.Repository}}:{{.Tag}}')
EOF
[ "$REMOVED" = 0 ] || echo "== removed $REMOVED old requi-full image tag(s)"
[ -z "$KEPT" ] || echo "== the newest code is already built: $KEPT is kept and will be smoke-checked before reuse"

# 4. build (only when the candidate tag is missing) and launch, with preview-exec's own gates: its typed yes,
# the build script's own yes, the pinned prices, and the health and execution checks, all unchanged.
if [ -n "${PREVIEW_EXEC_PROVIDER:-}" ]; then
  echo "== provider: $PREVIEW_EXEC_PROVIDER (from PREVIEW_EXEC_PROVIDER)"
else
  echo "== provider: preview-exec will ask you to pick: 1) ollama (local, \$0, heavy on this Mac) or 2) deepseek (paid, managed)"
fi
exec bash "${PREVIEW_EXEC_SCRIPT:-$REPO/scripts/ops/preview-exec.sh}"
