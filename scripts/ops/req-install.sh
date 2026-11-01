#!/usr/bin/env bash
# Install a pointer, never a copy of the evolving launcher. No network or database access.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
target="$repo/scripts/ops/req"
[ -f "$target" ] || { echo "REQ ERROR: project launcher missing at $target" >&2;exit 1; }
directory=${REQ_INSTALL_DIR:-$HOME/bin};mkdir -p "$directory"
file=$(mktemp "$directory/.req-pointer.XXXXXX");trap 'rm -f "$file"' EXIT
printf '#!/bin/sh\ntarget="%s/scripts/ops/req"\n[ -f "$target" ] || { echo "REQ ERROR: project launcher missing at $target. Restore checkout or reinstall pointer." >&2; exit 1; }\nexec bash "$target" "$@"\n' "$repo" > "$file"
chmod 755 "$file";mv -f "$file" "$directory/req"
echo "req points to $target; run hash -r in your shell."
