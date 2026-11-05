#!/usr/bin/env bash
# One repo-owned owner image workflow. Dry review is explicit. No automatic deploy.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -x .venv/bin/python ] || { printf '%s\n' '[FAIL] Install the checkout dependencies in .venv first.' >&2; exit 2; }
exec .venv/bin/python scripts/docker/image_workflow.py "$@"
