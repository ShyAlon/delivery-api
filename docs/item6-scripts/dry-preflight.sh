#!/usr/bin/env bash
# Reusable equivalent of the dry block used. No connected-run option.
set -euo pipefail
RUN=${1:?absolute existing run root required}
IMAGE=${2:?reviewed sha256 image ID required}
TOKEN=${3:?compiled runtime suffix required}
NAME=${4:?new dry-only container name required}
[[ "$RUN" == /* && "$IMAGE" =~ ^sha256:[a-f0-9]{64}$ && "$TOKEN" =~ ^[a-f0-9]{32}$ && "$NAME" =~ ^requi-item6-dry-[a-zA-Z0-9_-]+$ ]] || { echo 'Invalid explicit dry inputs'; exit 1; }
python3 "$RUN/private/verify-$TOKEN.py" "$RUN"
RUN_ID=$(python3 - "$RUN" <<'PYCODE'
import json,pathlib,sys
print(json.loads((pathlib.Path(sys.argv[1])/'run-id.json').read_text())['run_id'])
PYCODE
)
if docker container inspect "$NAME" >/dev/null 2>&1; then
 echo 'STOP: container already exists. Preserve and inspect it.'; exit 1
fi
docker image inspect "$IMAGE" --format '{{.Id}}'
docker run --name "$NAME" -it --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=2g \
 --mount "type=bind,src=$RUN/private/runtime-$TOKEN,dst=/private,readonly" \
 --mount "type=bind,src=$RUN/evidence,dst=/evidence" \
 "$IMAGE" --config /private/run.json --run-id "$RUN_ID"
docker inspect "$NAME" --format '{{.Name}} {{.State.Status}} exit={{.State.ExitCode}} image={{.Image}}'
