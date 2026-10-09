#!/usr/bin/env bash
set -euo pipefail
[ $# = 1 ] || { echo 'Usage: run-test-image.sh /path/to/prepared-context'; exit 2; }
command -v docker >/dev/null || { echo 'Docker Desktop or Docker Engine is required.'; exit 1; }
docker info >/dev/null
CONTEXT=$(cd "$1" && pwd)
[ -f "$CONTEXT/source-manifest.json" ] && [ -f "$CONTEXT/Dockerfile" ] || { echo 'Prepared integrated test context required.'; exit 1; }
IMAGE="requi-re-local-test:$(date +%s)"
docker build --tag "$IMAGE" "$CONTEXT"
# No host mounts, env file, provider secrets, exposed ports, or network at run.
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev --cap-drop ALL --security-opt no-new-privileges "$IMAGE"
echo "Integrated test image passed: $IMAGE"
