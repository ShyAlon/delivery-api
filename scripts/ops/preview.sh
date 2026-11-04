#!/usr/bin/env bash
# Compatibility name for the same image/runtime launcher. No execution-off fork.
set -euo pipefail
exec bash "$(dirname "$0")/preview-exec.sh" "$@"
