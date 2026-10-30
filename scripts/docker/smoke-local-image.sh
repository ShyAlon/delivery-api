#!/usr/bin/env bash
# One full-image proof for build and reuse: candidate wheels + owned disposable PG.
# A test-only derivative adds native PostgreSQL/migrations. Never tag/publish it as service.
# No inherited DSN, env file, provider key, host mount or external network at run.
set -euo pipefail
IMAGE=${1:-}; PLATFORM=${2:-}
case "$IMAGE" in requi-full:?*|requi-full-candidate:?*) ;; *) echo 'smoke-local-image: exact full-service tag required'; exit 2;; esac
case "$IMAGE" in *[!A-Za-z0-9:._-]*) echo 'smoke-local-image: invalid tag'; exit 2;; esac
case "$PLATFORM" in ''|linux/arm64|linux/amd64) ;; *) echo 'smoke-local-image: unsupported platform'; exit 2;; esac
HERE=$(cd "$(dirname "$0")/../.." && pwd)
D=(docker); [ "${REQUI_SMOKE_SUDO:-0}" != 1 ] || D=(sudo -n docker)
P=(); [ -z "$PLATFORM" ] || P=(--platform "$PLATFORM")
TEST_IMAGE=requi-pg-smoke-local:$$-$(date +%s)
CTX=$(mktemp -d); chmod 755 "$CTX"
cleanup(){ rc=$?; trap - EXIT; "${D[@]}" rmi "$TEST_IMAGE" >/dev/null 2>&1 || true; rm -rf "$CTX"; exit "$rc"; }
trap cleanup EXIT
# Minimal public source context only. Never send an untracked .env, whole checkout,
# auth state or artifact data to the Docker daemon, even under remote sudo.
mkdir -p "$CTX/tests" "$CTX/supabase/migrations" "$CTX/scripts/docker"
cp "$HERE/tests/pg_harness.py" "$HERE/tests/pg_guardian.py" "$CTX/tests/"
cp "$HERE/supabase/migrations/"*.sql "$CTX/supabase/migrations/"
cp "$HERE/scripts/docker/owned-pg-smoke.py" "$HERE/scripts/docker/system-pg-smoke.py" "$HERE/scripts/docker/Dockerfile.pg-smoke" "$CTX/scripts/docker/"
chmod -R go+rX "$CTX"
echo '== build test-only PG smoke derivative (service image unchanged)'
"${D[@]}" build "${P[@]}" --build-arg "SERVICE_IMAGE=$IMAGE" -f "$CTX/scripts/docker/Dockerfile.pg-smoke" -t "$TEST_IMAGE" "$CTX"
echo '== run candidate entry with owned migrated PG (network none, no keys, restricted DB login)'
rc=0
OUT=$("${D[@]}" run --rm "${P[@]}" --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 "$TEST_IMAGE" \
  python /smoke/owned-pg-smoke.py /smoke python /app/full-smoke.py 2>&1) || rc=$?
[ -z "$OUT" ] || printf '%s\n' "$OUT"
if [ "$rc" != 0 ] && [ -z "$OUT" ]; then echo "smoke container produced no output (docker exit code $rc): possible signal kill or out of memory"; fi
[ "$rc" = 0 ] || echo "owned PG smoke container exited with docker exit code $rc (a silent exit can be a signal/OOM kill)"
exit "$rc"
