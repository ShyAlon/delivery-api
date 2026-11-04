#!/usr/bin/env bash
# Rebuild is now explicit in the one image workflow. Never delete a running preview.
set -euo pipefail
printf '%s\n' '[INFO] Use req image --accept-local to build/test, then req preview --apply to start that exact image.' >&2
exec bash "$(dirname "$0")/image.sh" --accept-local "$@"
