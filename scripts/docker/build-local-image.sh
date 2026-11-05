#!/usr/bin/env bash
# Compatibility alias: one locally built linux/amd64 candidate, no native fork.
set -euo pipefail
exec bash "$(dirname "$0")/../ops/image.sh" --build "$@"
