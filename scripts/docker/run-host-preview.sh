#!/usr/bin/env bash
set -euo pipefail
[ $# = 1 ] || { echo 'Usage: run-host-preview.sh /path/to/ui-test-context'; exit 2; }
command -v docker >/dev/null || { echo 'Docker Desktop/Engine required'; exit 1; }
CONTEXT=$(cd "$1" && pwd)
[ -f "$CONTEXT/host-preview.py" ] || { echo 'UI test context required'; exit 1; }
VERSION=$(docker version --format '{{.Server.Version}}')
MAJOR=${VERSION%%.*}
[[ "$MAJOR" =~ ^[0-9]+$ ]] && [ "$MAJOR" -ge 28 ] || { echo 'Docker Engine28+ required for localhost publication safety'; exit 1; }
WEB=${PREVIEW_WEB_PORT:-8000};BACK=${PREVIEW_BACKOFFICE_PORT:-8020}
for PORT in "$WEB" "$BACK"; do [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || { echo 'Preview ports must be1024..65535'; exit 1; }; done
[ "$WEB" != "$BACK" ] || { echo 'Preview ports must differ'; exit 1; }
# Mac ships shasum; Linux commonly ships sha256sum. Hash names and bytes,
# not mtimes. Reject symlinks: image content must match the hashed context.
if command -v sha256sum >/dev/null; then SHA=(sha256sum)
else command -v shasum >/dev/null || { echo 'SHA256 utility required'; exit 1; }; SHA=(shasum -a 256); fi
[ -z "$(find "$CONTEXT" -type l -print)" ] || { echo 'Preview context must not contain symlinks'; exit 1; }
DIGEST=$(cd "$CONTEXT" && find . -type f -exec "${SHA[@]}" {} \; | LC_ALL=C sort | "${SHA[@]}" | awk '{print $1}')
IMAGE="requi-re-ui-preview:$DIGEST"
NET="requi-ui-preview-$$";NAME="requi-ui-preview-$$"
cleanup(){ docker rm -f "$NAME" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
 printf 'Reusing content-keyed image %s\n' "$IMAGE"
else
 docker build --tag "$IMAGE" "$CONTEXT"
fi
# Automated Chromium gate has no published ports and no runtime network.
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev --cap-drop ALL --security-opt no-new-privileges "$IMAGE"
# Internal bridge isolates internet egress, explicit loopback publication only.
docker network create --internal "$NET" >/dev/null
docker run -d --name "$NAME" --network "$NET" --read-only --tmpfs /tmp:rw,nosuid,nodev --cap-drop ALL --security-opt no-new-privileges -p "127.0.0.1:$WEB:8000" -p "127.0.0.1:$BACK:8020" "$IMAGE" python /app/host-preview.py >/dev/null
for ATTEMPT in $(seq 1 60); do
 if curl --fail --silent "http://127.0.0.1:$WEB/health" >/dev/null && curl --fail --silent "http://127.0.0.1:$BACK/health" >/dev/null; then break; fi
 sleep 1
done
curl --fail --silent "http://127.0.0.1:$WEB/health" >/dev/null
curl --fail --silent "http://127.0.0.1:$BACK/health" >/dev/null
printf 'Fake preview verified at host ports. Open:\nWeb: http://127.0.0.1:%s/preview\nBack office: http://127.0.0.1:%s/preview\nPress Ctrl-C to remove preview container and network.\n' "$WEB" "$BACK"
docker logs --follow "$NAME"
