#!/usr/bin/env bash
# Build the full Requi.re image LOCALLY on this Mac from the same five exact commits the server build uses.
# Run from a Mac: bash scripts/docker/build-local-image.sh
# Reads: the four local checkouts (clean, on main, equal to origin/main). Writes: one docker image on this Mac. No secrets, no provider call, no server contact.
# The image is this Mac's native architecture (arm64 on Apple Silicon). The deployed image is still the server's amd64 build:
# a local image proves the code, not architecture parity. PREVIEW_ARCH=amd64 forces an emulated amd64 build (much slower first build).
set -euo pipefail
abort(){ echo; echo "ABORTED [$1]: $2"; echo "Nothing was started unless it says so above."; echo "Fix: ${3:-run again; if it repeats, send me this whole output}"; echo "Then run: ${4:-bash scripts/docker/build-local-image.sh}"; exit 1; }
umask 022
HERE=$(cd "$(dirname "$0")/../.." && pwd); ROOT=${REQUI_REPOS_ROOT:-$(dirname "$HERE")}
for t in git docker tar shasum; do command -v $t >/dev/null || abort "TOOL-$t" "$t is missing" "xcode-select --install (Docker: open Docker Desktop once, or brew install --cask docker)"; done
docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"
MACHINE=$(uname -m); case "$MACHINE" in arm64|aarch64) NATIVE=arm64;; x86_64|amd64) NATIVE=amd64;; *) abort ARCH "unknown machine architecture $MACHINE";; esac
ARCH=${PREVIEW_ARCH:-$NATIVE}; case "$ARCH" in arm64|amd64) ;; *) abort ARCH "PREVIEW_ARCH must be arm64 or amd64, not '$ARCH'" "unset PREVIEW_ARCH";; esac
PLATFORM=linux/$ARCH
STAMP=$(date -u +%Y%m%d%H%M%S); CTX=$(mktemp -d "${TMPDIR:-/tmp}/requi-build.XXXXXXXXXX"); trap 'rm -rf "$CTX"' EXIT
TAG=""; REPOS=""
for r in delivery-api interview-engine spec-generator verification-loop; do
  D=$ROOT/$r; [ -d "$D/.git" ] || abort "NO-$r" "no checkout at $D" "git clone https://github.com/ShyAlon/$r $D"
  [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes" "git -C $D status --short"
  [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" = main ] || abort "BRANCH-$r" "$r is not on main" "git -C $D checkout main"
  git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "git -C $D fetch origin main"
  [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r main is not equal to origin/main" "git -C $D pull --ff-only origin main"
  mkdir "$CTX/$r"; git -C "$D" archive HEAD | tar -x -C "$CTX/$r"
  S=$(git -C "$D" rev-parse --short=8 HEAD); TAG="${TAG:+$TAG-}$S"; REPOS="${REPOS:+$REPOS,}$r=$S"; printf '  %-18s %s\n' "$r" "$S"
done
chmod -R u+rwX,go+rX "$CTX"   # sources only: no secret is ever in this folder
[ -f "$CTX/delivery-api/Dockerfile.full" ] || abort NO-DOCKERFILE "delivery-api main has no Dockerfile.full" "merge the Dockerfile.full PR first"
IMAGE=requi-full:$TAG; CAND=requi-full-candidate:$TAG
echo "image tag: $IMAGE   platform: $PLATFORM (local build, no server contact)"
if [ "$ARCH" != "$NATIVE" ]; then
  docker buildx version >/dev/null 2>&1 || abort NO-BUILDX "an emulated $ARCH build needs docker buildx" "unset PREVIEW_ARCH to build natively ($NATIVE)"
  echo "== PREVIEW_ARCH=$ARCH: emulated build, the first one can take a long time"
fi
# preview-exec collects the one typed yes itself and passes REQUI_BUILD_YES=1: asking a second
# yes here trained operators to answer a prompt they had already answered (2026-10-27 approved drop).
if [ "${REQUI_BUILD_YES:-}" = 1 ]; then
  echo "== the calling preview run already confirmed this build (REQUI_BUILD_YES=1); not asking again"
else
  printf 'This builds the four source trees (no keys, a few MB) into a local image. Type "yes" to continue: '
  read -r A < "${BUILD_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "run again and type yes"
fi
docker build --build-arg REQUI_BUILD_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" --platform "$PLATFORM" --build-arg REQUI_BUILD_ID="local-$STAMP" --build-arg REQUI_BUILD_REPOS="$REPOS" -f "$CTX/delivery-api/Dockerfile.full" -t "$CAND" "$CTX" || abort BUILD "the local build did not finish" "Nothing was tagged. The output above names the failing step."
echo "== smoke inside the image $CAND (no network, no provider key, not root)"
rc=0
SMOKE_OUT=$(bash "$HERE/scripts/docker/smoke-local-image.sh" "$CAND" "$PLATFORM" 2>&1) || rc=$?
[ -z "$SMOKE_OUT" ] || printf '%s\n' "$SMOKE_OUT"
if [ "$rc" -ne 0 ]; then
  [ -n "$SMOKE_OUT" ] || echo "== the smoke container produced no output at all (docker exit code $rc): it was killed before it could print - a signal kill, commonly the Docker Desktop VM running out of memory, leaves no output"
  REASON=$(printf '%s\n' "$SMOKE_OUT" | grep -E 'SMOKE FAILED at stage|disposable PG failed|[A-Za-z]+Error:|smoke stage:' | tail -n 1 | cut -c1-600) || true
  [ -n "$REASON" ] || REASON="no diagnostic failure line; docker exit code $rc"
  echo "smoke FAILED for $CAND (docker exit code $rc; output above)"
  if docker rmi "$CAND" >/dev/null 2>&1; then echo "removed the failed image tag $CAND (only that tag)"; abort SMOKE "the candidate image failed its smoke check and was removed. Failing check: $REASON" "Nothing was tagged. The output above names the failing check."; fi
  abort SMOKE "the candidate image failed its smoke check and could not be removed (a container may be using it); it is NOT approved and was left in place. Failing check: $REASON" "" "docker rmi $CAND   (then run again)"
fi
echo "smoke passed: $CAND"
EXIST=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)
docker tag "$CAND" "$IMAGE" || abort TAG "could not tag $IMAGE" "" "docker image ls requi-full"
docker rmi "$CAND" >/dev/null 2>&1 || true
[ -z "$EXIST" ] || [ "$EXIST" = "$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)" ] || echo "== replaced the previous local $IMAGE (that exact tag only)"
echo "== built $IMAGE locally ($PLATFORM; deployment still uses the server's amd64 build)"
