#!/usr/bin/env bash
# Compatibility alias: explicit repository/host/receipt review in the one workflow.
set -euo pipefail
printf '%s\n' '[INFO] Use req image --deploy ghcr.io/OWNER/PACKAGE --host USER@HOST to review. Add --apply only for owner-confirmed deployment.' >&2
# Do not choose an unverified package or target. Forward exact owner parameters.
exec bash "$(dirname "$0")/image.sh" "$@"
