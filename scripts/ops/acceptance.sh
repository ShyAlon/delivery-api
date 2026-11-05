#!/usr/bin/env bash
# Repo-owned acceptance entry. req delegates here; no separate implementation.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -x .venv/bin/python ] || { echo 'Acceptance not started: install the checkout dependencies in .venv first.' >&2;exit 1; }
if [ "${1:-}" = --image-local ]; then
  shift
  exec .venv/bin/python scripts/docker/image-local-acceptance.py "$@"
fi
exec .venv/bin/python scripts/owner/deepseek-acceptance.py "$@"
