#!/usr/bin/env bash
# The one smoke check for a full Requi.re image on the server. Used by build-full-image.sh (on the candidate), ops preview and ops deploy-full (before reusing a tag).
# Usage: bash scripts/docker/smoke-image.sh <requi-full[-candidate]:tag>
# Runs the image with no network, no provider key and only its own disposable database, as its own non-root user, read-only. Starts nothing that stays running.
# Exit 0: passed. Exit 1: FAILED, and that exact tag was removed from the server (nothing else is touched). Exit 2: could not decide or could not remove it.
set -uo pipefail
HOST=${REQUI_SERVER:-ubuntu@15.235.101.85}; IMAGE=${1:-}
case "$IMAGE" in requi-full:?*|requi-full-candidate:?*) ;; *) echo "smoke-image: refusing '$IMAGE': only exact requi-full tags are checked"; exit 2;; esac
case "$IMAGE" in *[!A-Za-z0-9:._-]*) echo "smoke-image: refusing '$IMAGE': unexpected characters"; exit 2;; esac
SSHB=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
echo "== smoke inside the image $IMAGE (no network, no provider key, not root)"
rc=0
# Upload only the supervisor/schema source, not settings or the checkout.
HERE=$(cd "$(dirname "$0")/../.." && pwd)
REMOTE_CTX=$("${SSHB[@]}" 'mktemp -d /tmp/requi-image-smoke.XXXXXXXXXX') || exit 2
[[ "$REMOTE_CTX" =~ ^/tmp/requi-image-smoke\.[A-Za-z0-9]+$ ]] || { echo 'smoke-image: invalid remote scratch'; exit 2; }
cleanup(){ "${SSHB[@]}" "rm -rf '$REMOTE_CTX'" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if ! tar -c -C "$HERE" scripts/docker/smoke-local-image.sh scripts/docker/Dockerfile.pg-smoke scripts/docker/owned-pg-smoke.py scripts/docker/system-pg-smoke.py tests/pg_harness.py tests/pg_guardian.py supabase/migrations | "${SSHB[@]}" "tar -x -C '$REMOTE_CTX'"; then
  echo 'smoke-image: cannot stage disposable PG harness; service tag unchanged'; exit 2
fi
OUT=$("${SSHB[@]}" "REQUI_SMOKE_SUDO=1 bash '$REMOTE_CTX/scripts/docker/smoke-local-image.sh' '$IMAGE'" 2>&1) || rc=$?
[ -z "$OUT" ] || printf '%s\n' "$OUT"
[ "$rc" -ne 0 ] || { echo "smoke passed: $IMAGE"; exit 0; }
if [ "$rc" = 255 ]; then echo "smoke-image: cannot reach $HOST; nothing was changed"; exit 2; fi
[ -n "$OUT" ] || echo "== the smoke container produced no output at all (exit code $rc): it was killed before it could print - a signal kill, commonly out of memory, leaves no output"
echo "smoke FAILED for $IMAGE (exit code $rc; output above)"
if "${SSHB[@]}" "sudo -n docker rmi $IMAGE" >/dev/null 2>&1; then echo "removed the failed image tag $IMAGE from the server (only that tag)"; exit 1; fi
echo "could not remove $IMAGE (a container may be using it); it is NOT approved and was left in place"; exit 2
