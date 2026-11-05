#!/usr/bin/env bash
# Compatibility name only: the accepted image and shared runtime, no rebuild path.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec bash "$HERE/image.sh" --preview --name requi-preview-exec --port "${PREVIEW_EXEC_PORT:-8001}" --env-file "${REQUI_PREVIEW_ENV:-$HOME/.config/requi/preview.env}" "$@"
