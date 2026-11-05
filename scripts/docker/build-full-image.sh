#!/usr/bin/env bash
# Compatibility alias. Remote source builds are retired, not a fallback.
set -euo pipefail
printf '%s\n' '[INFO] Remote builds retired. Building one linux/amd64 image locally; no server contact.' >&2
exec bash "$(dirname "$0")/../ops/image.sh" --build "$@"
