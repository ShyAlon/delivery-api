#!/usr/bin/env bash
# Build the full Requi.re image on the OVH server from five exact commits. Run from a Mac: bash scripts/docker/build-full-image.sh
# Reads: the five local checkouts (clean, on main, equal to origin/main). Writes: ~/requi/build-<time>/ and one docker image on the server. No secrets, no provider call.
set -euo pipefail
# The build context must not depend on the caller's umask (a private umask made every source file mode 600, unreadable to the non-root image user).
umask 022
HOST=${REQUI_SERVER:-ubuntu@15.235.101.85}
abort(){ echo; echo "ABORTED [$1]: $2"; echo "Nothing was started on the server unless it says so above."; echo "Fix: ${3:-run again; if it repeats, send me this whole output}"; echo "Then run: req"; exit 1; }
HERE=$(cd "$(dirname "$0")/../.." && pwd); ROOT=${REQUI_REPOS_ROOT:-$(dirname "$HERE")}
for t in git ssh tar shasum; do command -v $t >/dev/null || abort "TOOL-$t" "$t is missing" "xcode-select --install"; done
STAMP=$(date -u +%Y%m%d%H%M%S); CTX=$(mktemp -d); trap 'rm -rf "$CTX"' EXIT
TAG=""
for r in delivery-api interview-engine spec-generator verification-loop; do
  D=$ROOT/$r; [ -d "$D/.git" ] || abort "NO-$r" "no checkout at $D" "git clone https://github.com/ShyAlon/$r $D"
  [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes" "git -C $D status --short"
  [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" = main ] || abort "BRANCH-$r" "$r is not on main" "git -C $D checkout main"
  git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "git -C $D fetch origin main"
  [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r main is not equal to origin/main" "git -C $D pull --ff-only origin main"
  mkdir "$CTX/$r"; git -C "$D" archive HEAD | tar -x -C "$CTX/$r"
  S=$(git -C "$D" rev-parse --short=8 HEAD); TAG="${TAG:+$TAG-}$S"; printf '  %-18s %s\n' "$r" "$S"
done
chmod -R u+rwX,go+rX "$CTX"   # sources only: no secret is ever in this folder
[ -f "$CTX/delivery-api/Dockerfile.full" ] || abort NO-DOCKERFILE "delivery-api main has no Dockerfile.full" "merge the Dockerfile.full PR first"
IMAGE=requi-full:$TAG; CAND=requi-full-candidate:$TAG
echo "image tag: $IMAGE   server: $HOST   build dir: ~/requi/build-$STAMP"
printf 'This uploads the four source trees (no keys, a few MB) over ssh and builds the image on the server. Type yes to continue: '
read -r A < "${BUILD_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "run again and type yes"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" 'sudo -n docker info >/dev/null && echo ok' | grep -q ok || abort SSH-DOCKER "cannot ssh to $HOST or ubuntu cannot run sudo docker" "ssh $HOST 'sudo -n docker info' | head -3"
ssh "$HOST" "mkdir -p ~/requi && chmod 700 ~/requi && for d in ~/requi/build-[0-9]*; do [ -d \"\$d\" ] && rm -rf \"\$d\"; done; mkdir ~/requi/build-$STAMP" || abort MKDIR "cannot create the build folder"
tar -c -C "$CTX" . | ssh "$HOST" "tar -x -p -C ~/requi/build-$STAMP" || abort UPLOAD "upload failed"
echo "== docker build (several minutes the first time)"
ssh "$HOST" "cd ~/requi/build-$STAMP && sudo -n docker build --build-arg REQUI_BUILD_ID=$TAG -f delivery-api/Dockerfile.full -t $CAND . > ../build-$STAMP.log 2>&1; rc=\$?; tail -15 ../build-$STAMP.log; exit \$rc" || { ssh "$HOST" "rm -rf ~/requi/build-$STAMP" || true; abort BUILD "docker build failed (log: $HOST:~/requi/build-$STAMP.log)" "The image tag $IMAGE was not created; the uploaded sources were removed from the server."; }
SRC=0; REQUI_SERVER="$HOST" bash "$HERE/scripts/docker/smoke-image.sh" "$CAND" || SRC=$?
if [ "$SRC" -ne 0 ]; then
  ssh "$HOST" "rm -rf ~/requi/build-$STAMP" || true
  abort SMOKE "the smoke check failed (output above)" "send me this output. $([ "$SRC" = 1 ] && echo "The candidate image was removed from the server;" || echo "The candidate image $CAND could NOT be removed;") $IMAGE was not created."
fi
ssh "$HOST" "sudo -n docker tag $CAND $IMAGE && sudo -n docker rmi $CAND >/dev/null" || abort TAG "the image passed the smoke check but could not be tagged as $IMAGE" "send me this output (the candidate is $CAND on the server)"
echo; echo "DONE. Image $IMAGE built and smoke-tested (the tag exists only because it passed) on $HOST. Nothing is running."

