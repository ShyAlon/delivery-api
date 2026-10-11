# Reusable appendix template derived from the actual staged blocks.
# Replace absolute repo path/image tag with reviewed inputs; not paste-ready.
# Run on Mac. Existing run/.env preserved. No --go. No capture or provider run.
# Block 1: extract NEW package directory and rebuild a NEW image tag.
mkdir -m 700 ~/Downloads/requi-item6-receiver-build
unzip ~/Downloads/requi-item6-receiver-merged.zip -d ~/Downloads/requi-item6-receiver-build
export PKG="$HOME/Downloads/requi-item6-receiver-build/requi-item6-receiver-merged"
cd "$PKG"
python3 - <<'PY'
import hashlib,json,pathlib
root=pathlib.Path('.')
m=json.loads((root/'source-manifest.json').read_text())
for name,value in m.items():
    if name.endswith('.whl'):
        assert hashlib.sha256((root/'wheels'/name).read_bytes()).hexdigest()==value['sha256']
print(json.dumps({'checked':'package sources/wheels','expected':'merged delivery6f8c81d and exact wheel hashes','found':m,'where':'new package','run_started':False}))
PY
docker build -t requi-item6-receiver:REVIEWED_COMMIT .
docker image inspect requi-item6-receiver:REVIEWED_COMMIT --format '{{.Id}}'
# Paste final build tail/inspect ID. Stop on failure; don't continue other blocks.

# Block 2: fresh helper environment, same existing run. No reimport/migration.
cd "$PKG"
python3 -m venv .helpers
.helpers/bin/python -m pip install -c constraints.txt wheels/*.whl 'psycopg[binary]'
.helpers/bin/python -m pip check
export PY="$PKG/.helpers/bin/python"
cd /ABSOLUTE/PATH/TO/delivery-api
export RUN="$PWD/runs/item6-run-01"
"$PY" "$PKG/item6/scaffold.py" diagnose-review "$RUN"
# Paste only safe diagnostic output. If not ready, stop; retain all files.

# Block 3: review without overwrite. Output includes nonsecret config, keys hidden.
umask 077
set -o noclobber
"$PY" "$PKG/item6/scaffold.py" review-config "$RUN" > "$RUN/private/receiver-review-result.json"
cat "$RUN/private/receiver-review-result.json"
# Inspect locally; paste safe review output only. Continue ONLY on review_hash success.

# Block 4: after checking review output locally, confirm exact CURRENT hash/compile.
export REVIEW_HASH="$("$PY" -c 'import json,os;print(json.load(open(os.environ["RUN"]+"/private/receiver-review-result.json"))["review_hash"])')"
"$PY" "$PKG/item6/scaffold.py" review-config "$RUN" --confirm "$REVIEW_HASH"
"$PY" "$PKG/item6/scaffold.py" compile-config "$RUN"
# Paste confirm/compile JSON. No launcher execution yet.

# Block 5: read-only container state. Preserve existing stopped/running containers.
docker ps -a --filter name=requi-item6 --format '{{.Names}} {{.Status}} {{.Image}}'
# Paste container list; later dry launcher uses NEW image ID and reconciled name.
# Do not tag over/remove old image/container or run launcher until exact mechanics are supplied.
