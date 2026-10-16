#!/usr/bin/env bash
# One driver for the item 6 run on the Linux server. Runs on your Mac. Usage:
#   bash scripts/item6/vps/item6-run.sh [--rebuild]          full flow (one yes, then the GO phrase)
#   bash scripts/item6/vps/item6-run.sh --resume RUN_DIR attach|status|collect|webhook
set -euo pipefail
HOST=15.235.101.85
DELIVERY=${DELIVERY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
UBU=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes ubuntu@$HOST)
abort(){ echo; echo "ABORTED [$1]: $2"; echo "${3:-Nothing was changed on the server.}"; exit 1; }
gate(){ :; }   # the single yes is asked once, below
STATE=$(mktemp -d); trap 'rm -rf "$STATE"' EXIT
stage_build(){
PARENT=$(cd "$DELIVERY/.." && pwd); TS=$(date +%Y%m%d%H%M%S); PKG=$PARENT/item6-package-$TS
echo "== Mac checks"
for t in python3 git uv ssh tar shasum; do command -v $t >/dev/null || abort "MAC-TOOL-$t" "$t is not installed (prepare-package.py needs git and uv)"; done
[ -f "$DELIVERY/scripts/item6/prepare-package.py" ] || abort MAC-CHECKOUT "no prepare-package.py under $DELIVERY/scripts/item6"
for r in delivery-api interview-engine spec-generator vmodel-contracts; do
  d=$PARENT/$r; [ -d "$d/.git" ] || abort "MAC-REPO-$r" "$d is not a git checkout"
  git -C "$d" fetch -q origin || abort "MAC-FETCH-$r" "git fetch failed for $r"
  [ "$(git -C "$d" rev-parse --abbrev-ref HEAD)" = main ] || abort "MAC-BRANCH-$r" "$r is not on main"
  [ -z "$(git -C "$d" status --porcelain --untracked-files=no)" ] || abort "MAC-DIRTY-$r" "$r has uncommitted tracked changes"
  [ "$(git -C "$d" rev-list --count HEAD..origin/main)" = 0 ] || abort "MAC-BEHIND-$r" "$r is behind origin/main (git pull --ff-only)"
  [ "$(git -C "$d" rev-list --count origin/main..HEAD)" = 0 ] || abort "MAC-UNPUSHED-$r" "$r has commits not on origin/main"
  echo "ok  $r @ $(git -C "$d" rev-parse --short=10 HEAD)"
done
echo "== preflight (read-only) on ubuntu@$HOST"
TMPF=$(mktemp); trap 'rm -f "$TMPF"' EXIT
if ! "${UBU[@]}" 'bash -s' >"$TMPF" <<'PRE'
set -u
echo "sudo=$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
echo "dockerinfo=$(sudo -n docker info >/dev/null 2>&1 && echo yes || echo no)"
echo "uid=$(id -u)"
echo "free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
echo "mem_mb=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)"
echo "image=$(sudo -n docker image inspect requi-item6-test >/dev/null 2>&1 && echo exists || echo none)"
echo "containers=$(sudo -n docker ps -a --format '{{.Names}}' 2>/dev/null | grep -c '^requi-item6' || true)"
PRE
then abort VPS-SSH "cannot ssh to ubuntu@$HOST with your default key (same login the Caddy step used)"; fi
PRE=$(cat "$TMPF")
g(){ printf '%s\n' "$PRE" | sed -n "s/^$1=//p" | head -1; }
[ "$(g sudo)" = yes ] || abort VPS-SUDO "ubuntu has no passwordless sudo"
[ "$(g dockerinfo)" = yes ] || abort VPS-DOCKER "'sudo -n docker info' fails for ubuntu"
[ "$(g uid)" = 1000 ] || abort VPS-UID "ubuntu is uid $(g uid), but the image user is 1000 (private files would be unreadable in the container)"
[ "$(g free_gb)" -ge 10 ] 2>/dev/null || abort VPS-DISK "only $(g free_gb) GB free; the build needs about 10"
[ "$(g mem_mb)" -ge 1500 ] 2>/dev/null || abort VPS-MEM "only $(g mem_mb) MB available for the build"
if false; then abort IMAGE-EXISTS "image requi-item6-test already exists on the server" "Rebuild deliberately with: REBUILD=1 bash item6-build.sh"; fi
echo "ok  sudo, docker, uid 1000, disk $(g free_gb) GB, memory $(g mem_mb) MB, image: $(g image), leftover item6 containers: $(g containers)"
gate "
About to:
   1. build the exact package from your four repos into $PKG on this Mac (offline wheel build, no network, no secrets)
   2. upload it (a few MB, no keys) to ubuntu@$HOST:~/item6/pkg-$TS
   3. run 'sudo docker build -t requi-item6-test' there: downloads the python base image, Debian postgresql-15 and pip packages,
      takes several minutes and about 2 GB of disk. Nothing running on the server (app, Caddy, webhook receiver) is touched.
   4. smoke-test the image (imports, postgres binary, uid), no network, no database, no run"
echo "== package"
PY=python3; [ -x "$DELIVERY/.venv/bin/python" ] && PY=$DELIVERY/.venv/bin/python
if ! OUT=$(cd "$DELIVERY" && "$PY" scripts/item6/prepare-package.py --delivery "$DELIVERY" --engine "$PARENT/interview-engine" --generator "$PARENT/spec-generator" --contracts "$PARENT/vmodel-contracts" --output "$PKG" ${PREPARE_ARGS:-} 2>&1); then
  echo "$OUT" | tail -15; abort PREPARE-PACKAGE "prepare-package.py failed (see above). If uv complains about build isolation or offline, retry with: PREPARE_ARGS=--no-build-isolation bash item6-build.sh" "Nothing was changed on the server. Local $PKG may be partial: leave it, a new run uses a new directory."
fi
echo "$OUT" | tail -3; echo "source revisions:"; python3 -c "import json,sys;m=json.load(open('$PKG/source-manifest.json'));[print('  ',k,v['revision'][:12]) for k,v in m.items() if 'revision' in v]"
echo "== upload"
COPYFILE_DISABLE=1 tar czf - -C "$PKG" . | "${UBU[@]}" "umask 077; mkdir -p ~/item6 && mkdir ~/item6/pkg-$TS && tar xzf - -C ~/item6/pkg-$TS && chmod -R u+rwX,go+rX ~/item6/pkg-$TS" || abort UPLOAD "upload failed" "Remote ~/item6/pkg-$TS may be partial; nothing else changed."
L=$(shasum -a 256 "$PKG/source-manifest.json" | cut -d' ' -f1); W=$(shasum -a 256 "$PKG/wheel-sha256.txt" | cut -d' ' -f1)
R=$("${UBU[@]}" "cd ~/item6/pkg-$TS && sha256sum source-manifest.json wheel-sha256.txt | cut -d' ' -f1 | tr '\n' ' '")
[ "$R" = "$L $W " ] || abort UPLOAD-HASH "uploaded manifest/wheel list differ from the local package" "Do not build. Tell me."
echo "ok  uploaded and hashes match"
echo "== build on the server (several minutes)"
"${UBU[@]}" "TS=$TS bash -s" <<'B' || abort BUILD-FAILED "docker build failed (log: ubuntu@$HOST:~/item6/build-$TS.log). The failing step is shown above." "No image tag was replaced. Nothing running was touched."
set -eo pipefail
cd ~/item6/pkg-$TS
sudo -n docker build -t requi-item6-test . 2>&1 | tee ../build-$TS.log | tail -n 25
B
echo "== smoke test of the image (no network, no database, no run)"
"${UBU[@]}" 'bash -s' <<'S' || abort SMOKE-FAILED "the image built but the smoke test failed (see above). Nothing was run." "The image stays for inspection."
set -e
D="sudo -n docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=64m"
$D --entrypoint python requi-item6-test -c "import delivery_api,interview_engine,spec_generator,vmodel_contracts,psycopg;import os;print('imports ok; uid',os.getuid())"
$D --entrypoint /usr/lib/postgresql/15/bin/postgres requi-item6-test --version
$D --entrypoint python requi-item6-test -c "import os,sys;f='/app/item6/container_entry.py';open(f).read();bad=[os.path.join(r,n) for r,d,fs in os.walk('/app') for n in d+fs if not os.access(os.path.join(r,n),os.R_OK)];print('app readable by uid',os.getuid(),'unreadable:',len(bad),bad[:5]);sys.exit(1 if bad else 0)"
sudo -n docker image ls requi-item6-test --format 'image {{.ID}} {{.Size}} created {{.CreatedSince}}'
S
echo
}
stage_config(){
PY=$DELIVERY/.venv/bin/python; SRC=${SRC_RUN:-$DELIVERY/runs/item6-run-01}
sc(){ (cd "$DELIVERY/scripts/item6" && "$PY" scaffold.py "$@"); }
echo "== checks"
[ -x "$PY" ] || abort MAC-VENV "no $PY (the delivery-api virtualenv: review/compile import delivery_api)" "Nothing changed."
"$PY" -c 'import delivery_api, vmodel_contracts' 2>/dev/null || abort MAC-VENV-IMPORT "the delivery-api virtualenv cannot import delivery_api/vmodel_contracts" "After git pull, refresh it: cd $DELIVERY && ./scripts/test-local.sh (or pip install -e .). Nothing changed."
[ -f "$SRC/private/.env" ] || abort SRC-ENV "no $SRC/private/.env to copy the values from"
M=$(stat -c %a "$SRC/private/.env" 2>/dev/null || stat -f %Lp "$SRC/private/.env"); [ "$M" = 600 ] || abort SRC-MODE "$SRC/private/.env is mode $M, not 600"
git -C "$DELIVERY" check-ignore -q runs/x || abort RUNS-NOT-IGNORED "runs/ is not git-ignored, secrets could be committed"
i=2; while [ -e "$DELIVERY/runs/item6-run-$(printf %02d $i)" ]; do i=$((i+1)); done
NEW=$DELIVERY/runs/item6-run-$(printf %02d $i)
echo "ok  environment; new run directory will be $NEW"; printf "%s" "$NEW" > "$STATE/newdir"
gate "
About to (all on this Mac, nothing leaves it except two public GETs):
   1. create the fresh run directory $NEW (private, new identity and database name)
   2. copy PAYPAL_CLIENT_ID/SECRET, DEEPSEEK_API_KEY, SANDBOX_MERCHANT_ID, SANDBOX_WEBHOOK_ID from $SRC/private/.env into its .env
      (values are not displayed; the model is deepseek-flash)
   3. fetch the DeepSeek pricing and API docs pages (public, no key) into a vendor snapshot
   4. show you the review values, ask for the review hash, then compile the runtime configuration"
fail(){ echo; echo "STEP FAILED: $1"; echo "Named field checks (values never printed):"; sc diagnose-review "$NEW" 2>&1 | tail -n 40 || true; abort CONFIG-STEP "$1 failed (see above)." "Run directory $NEW is kept for inspection; a retry makes a new one. Nothing was sent anywhere."; }
sc create-env "$NEW" >/dev/null || fail create-env
SRC="$SRC/private/.env" DST="$NEW/private/.env" "$PY" - <<'PYEOF' || fail copy-values
import json,os,sys
keys=['PAYPAL_CLIENT_ID','PAYPAL_CLIENT_SECRET','DEEPSEEK_API_KEY','SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID']
vals={}
for line in open(os.environ['SRC']):
    k,s,v=line.partition('=')
    if s and k in keys:
        try: vals[k]=json.loads(v)
        except ValueError: pass
bad=[k for k in keys if not isinstance(vals.get(k),str) or not vals[k].strip() or 'REPLACE' in vals[k]]
if bad: print('missing or unresolved in the source .env:',', '.join(bad)); sys.exit(1)
vals['MODEL']='deepseek-flash'
dst=os.environ['DST']; tmp=dst+'.tmp'
fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:
    f.write('# Only human-edited run configuration. JSON scalars; no shell expansion.\n# null means unresolved. Never source this file into a shell.\n')
    for k in sorted(vals): f.write(k+'='+json.dumps(vals[k],ensure_ascii=True)+'\n')
os.replace(tmp,dst); print('copied 5 values + model (not displayed)')
PYEOF
echo "== vendor snapshot (public pages)"
sc fetch-vendor-snapshot "$NEW" --model deepseek-flash || fail fetch-vendor-snapshot
echo "== review"
REV=$(sc review-config "$NEW") || fail review-config
printf '%s\n' "$REV" | "$PY" -c 'import json,sys;d=json.loads(sys.stdin.read().strip().splitlines()[-1]);print(json.dumps({k:v for k,v in d.items() if k!="review_hash"},indent=1))'
HASH=$(printf '%s\n' "$REV" | "$PY" -c 'import json,sys;print(json.loads(sys.stdin.read().strip().splitlines()[-1])["review_hash"])')
echo; echo "Current UTC time: $(date -u '+%Y-%m-%d %H:%M')   (DeepSeek has peak/off-peak pricing: the run must stay inside ONE band; check https://api-docs.deepseek.com/quick_start/pricing/)"
echo "Review hash $HASH (confirmed by the single GO at the end; review the values above first)"
sc review-config "$NEW" --confirm "$HASH" >/dev/null || fail review-confirm
OUT=$(sc compile-config "$NEW") || fail compile-config
printf '%s\n' "$OUT" | "$PY" -c 'import json,sys;d=json.loads(sys.stdin.read().strip().splitlines()[-1]);print("compiled:",d.get("generated_runtime"));print("launcher:",d.get("dry_launcher"))'
echo
}
stage_ship(){
RUN=${1:-}; [ -d "$RUN" ] || abort USAGE "pass the compiled run directory, e.g. $DELIVERY/runs/item6-run-02" "Nothing changed."
RUN=$(cd "$RUN" && pwd); NAME=$(basename "$RUN")
echo "== Mac checks on $RUN"
RID=$(python3 -c "import json;d=json.load(open('$RUN/run-id.json'));assert d['run_started'] is False and d['launch_authorized'] is False;print(d['run_id'])" 2>/dev/null) || abort RUN-ID "run-id.json missing or not a fresh unstarted run"
[ -z "$(ls -A "$RUN/evidence")" ] || abort EVIDENCE-NOT-EMPTY "evidence/ is not empty: this run dir was already used" "Use a new run directory."
RT=$(ls -d "$RUN"/private/runtime-* 2>/dev/null | wc -l | tr -d ' '); [ "$RT" = 1 ] || abort RUNTIME-COUNT "expected exactly one private/runtime-* directory, found $RT (run item6-config.sh first)"
LC=$(ls "$RUN"/run-compiled-*.sh 2>/dev/null | wc -l | tr -d ' '); [ "$LC" = 1 ] || abort LAUNCHER-COUNT "expected exactly one run-compiled-*.sh, found $LC"
VF=$(ls "$RUN"/private/verify-*.py 2>/dev/null | wc -l | tr -d ' '); [ "$VF" = 1 ] || abort VERIFIER-COUNT "expected exactly one private/verify-*.py, found $VF"
[ -f "$RUN/private/.env" ] || abort NO-ENV "private/.env missing (the verifier hashes it)"
python3 "$RUN"/private/verify-*.py "$RUN" || abort VERIFY-LOCAL "the compiled-config verifier rejects this run directory" "Do not ship. Recompile in a new run directory."
BADM=$(find "$RUN" \( -type f ! -name '*.sh' ! -perm 600 \) -o \( -type d ! -perm 700 \) | wc -l | tr -d ' '); [ "$BADM" = 0 ] || abort LOCAL-MODES "$BADM file(s)/dir(s) in the run directory are not owner-only (600/700)"
LAUNCHER=$(ls "$RUN"/run-compiled-*.sh); LNAME=$(basename "$LAUNCHER"); LSHA=$(shasum -a 256 "$LAUNCHER" | cut -d' ' -f1)
grep -q '^docker run ' "$LAUNCHER" || abort LAUNCHER-SHAPE "the launcher has no line starting with 'docker run '" "The sed step would not apply; tell me."
echo "ok  run id $RID, one runtime dir, verifier passes, modes owner-only"
echo "== preflight (read-only) on ubuntu@$HOST"
TMPF=$(mktemp); trap 'rm -f "$TMPF"' EXIT
if ! "${UBU[@]}" "NAME=$NAME bash -s" >"$TMPF" <<'PRE'
set -u
echo "sudo=$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
echo "dockerinfo=$(sudo -n docker info >/dev/null 2>&1 && echo yes || echo no)"
echo "uid=$(id -u)"
echo "image=$(sudo -n docker image inspect requi-item6-test >/dev/null 2>&1 && echo yes || echo no)"
echo "target=$([ -e ~/item6/runs/$NAME ] && echo exists || echo free)"
echo "tmux=$(command -v tmux >/dev/null && echo yes || echo no)"
echo "py3=$(command -v python3 >/dev/null && echo yes || echo no)"
echo "free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
PRE
then abort VPS-SSH "cannot ssh to ubuntu@$HOST"; fi
PRE=$(cat "$TMPF"); g(){ printf '%s\n' "$PRE" | sed -n "s/^$1=//p" | head -1; }
[ "$(g sudo)" = yes ] && [ "$(g dockerinfo)" = yes ] || abort VPS-DOCKER "ubuntu cannot run 'sudo -n docker'"
[ "$(g uid)" = 1000 ] || abort VPS-UID "ubuntu is uid $(g uid), not 1000"
[ "$(g image)" = yes ] || abort NO-IMAGE "image requi-item6-test is not on the server" "Run item6-build.sh first."
[ "$(g target)" = free ] || abort TARGET-EXISTS "~/item6/runs/$NAME already exists on the server" "Never overwritten. Use a new run directory name."
[ "$(g tmux)" = yes ] || abort NO-TMUX "tmux is not installed for ubuntu (a dropped connection would kill the run)"
[ "$(g py3)" = yes ] || abort NO-PYTHON3 "python3 missing on the server (the verifier runs on the host)"
echo "ok  sudo docker, uid 1000, image present, tmux, python3, target free, $(g free_gb) GB free"
gate "
About to upload the run directory $NAME to ubuntu@$HOST:~/item6/runs/$NAME (ssh stdin only).
It contains private/.env and the runtime credentials (PayPal sandbox app keys and your DeepSeek key), owner-only on the server (ubuntu, 600/700),
and stays there until you delete it. Then: create run-vps.sh, run the harness DRY preflight (no network, no database, no payment,
no model call), and remove only that exited dry container. The real run needs a separate step and your exact go."
echo "== upload"
COPYFILE_DISABLE=1 tar cf - -C "$(dirname "$RUN")" "$NAME" | "${UBU[@]}" "umask 077; mkdir -p ~/item6/runs && tar xpf - -C ~/item6/runs" || abort UPLOAD "upload failed" "~/item6/runs/$NAME may be partial on the server: it is not used; tell me before deleting it."
echo "== verify byte-for-byte"
(cd "$RUN" && find . -type f | sort | while IFS= read -r f; do shasum -a 256 "$f"; done) > "$TMPF.l"
"${UBU[@]}" "cd ~/item6/runs/$NAME && find . -type f | sort | while IFS= read -r f; do sha256sum \"\$f\"; done" > "$TMPF.r" || abort VERIFY-REMOTE "could not hash the uploaded files"
cmp -s "$TMPF.l" "$TMPF.r" || { diff "$TMPF.l" "$TMPF.r" | sed 's/[0-9a-f]\{64\}/<hash>/g' | head; rm -f "$TMPF.l" "$TMPF.r"; abort UPLOAD-HASH "uploaded files differ from the local run directory (names above, hashes hidden)" "Do not run it. Tell me."; }
rm -f "$TMPF.l" "$TMPF.r"
echo "ok  $(cd "$RUN" && find . -type f | wc -l | tr -d ' ') files identical"
echo "== server-side modes, owner, launcher"
"${UBU[@]}" "NAME=$NAME LNAME=$LNAME LSHA=$LSHA bash -s" <<'FIX' || abort SERVER-SETUP "server-side check or launcher step failed (see above)" "Run directory stays on the server, unused."
set -eu
cd ~/item6/runs/$NAME
[ -z "$(find . -name '._*' -o -name '.DS_Store')" ] || { echo "stray Mac metadata files in the upload"; exit 1; }
[ -z "$(find . ! -uid 1000)" ] || { echo "files not owned by uid 1000"; exit 1; }
[ -z "$(find . -type f ! -name '*.sh' -perm /077)" ] || { echo "group/other permissions on private files"; exit 1; }
[ -z "$(ls -A evidence)" ] || { echo "evidence not empty"; exit 1; }
[ "$(sha256sum "$LNAME" | cut -d' ' -f1)" = "$LSHA" ] || { echo "launcher hash differs"; exit 1; }
sed 's/^docker run /sudo -n docker run --memory=2800m --memory-swap=2800m --pids-limit=512 /' "$LNAME" > run-vps.sh.tmp
[ "$(diff "$LNAME" run-vps.sh.tmp | grep -c '^>')" = 1 ] || { echo "unexpected launcher diff"; exit 1; }
[ "$(grep -c '^sudo -n docker run ' run-vps.sh.tmp)" = 1 ] || { echo "docker line not rewritten exactly once"; exit 1; }
bash -n run-vps.sh.tmp; chmod 700 run-vps.sh.tmp; mv run-vps.sh.tmp run-vps.sh
echo "ok  owner/modes fine; run-vps.sh = launcher with only the docker line changed:"; diff "$LNAME" run-vps.sh | sed 's/^/     /' | cut -c1-200 || true
python3 private/verify-*.py . && echo "ok  verifier passes on the server"
FIX
echo "== DRY preflight on the server (no --go)"
if ! "${UBU[@]}" -tt "cd ~/item6/runs/$NAME && bash run-vps.sh"; then
  "${UBU[@]}" "sudo -n docker logs requi-item6-$RID 2>&1 | tail -n 25" || true
  abort DRY-PREFLIGHT "the harness dry preflight failed (container log above)" "The exited dry container requi-item6-$RID is kept for inspection. Nothing paid, no database, no network call was made."
fi
echo "== check dry result and clean up only the dry container"
"${UBU[@]}" "NAME=$NAME RID=$RID bash -s" <<'DRY' || abort DRY-CHECK "dry-result check failed (see above)" "Kept the container; nothing removed."
set -eu
cd ~/item6/runs/$NAME
[ -z "$(ls -A evidence)" ] || { echo "evidence is not empty after a dry run: not removing anything"; exit 1; }
st=$(sudo -n docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' requi-item6-$RID)
[ "$st" = "exited 0" ] || { echo "container state is '$st'"; exit 1; }
sudo -n docker logs requi-item6-$RID 2>&1 | grep -q 'item6-dry-preflight' || { echo "no dry-preflight receipt in the log"; exit 1; }
sudo -n docker logs requi-item6-$RID 2>&1 | grep -q '"network_started": false' || { echo "dry receipt does not say network_started false"; exit 1; }
sudo -n docker rm requi-item6-$RID >/dev/null && echo "ok  dry container requi-item6-$RID removed (it was exited 0, evidence empty)"
DRY
echo
}
stage_launch(){
CEILING=2.00
RUN=${1:-}; MODE=${2:-}
[ -d "$RUN" ] && [ -n "$MODE" ] || abort USAGE "usage: bash item6-launch.sh /path/to/runs/item6-run-NN go|attach|webhook|collect|status" "Nothing changed."
RUN=$(cd "$RUN" && pwd); NAME=$(basename "$RUN")
RID=$(python3 -c "import json;print(json.load(open('$RUN/run-id.json'))['run_id'])" 2>/dev/null) || abort RUN-ID "cannot read run-id.json"
SESS=item6-${RID:0:8}; RDIR='~/item6/runs/'$NAME
case "$MODE" in
status)
  "${UBU[@]}" "RID=$RID SESS=$SESS bash -s" <<S
cd $RDIR 2>/dev/null || { echo "run dir not on the server"; exit 1; }
echo "tmux: \$(tmux ls 2>/dev/null | grep \$SESS || echo 'no session')"
echo "container: \$(sudo -n docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}} started={{.State.StartedAt}}' requi-item6-\$RID 2>/dev/null || echo none)"
echo "--- watcher log (tail)"; tail -n 8 watch-*.log 2>/dev/null || echo none
echo "--- evidence files"; find evidence -maxdepth 2 | head -30
S
  ;;
attach)
  "${UBU[@]}" -tt "tmux attach -t $SESS" || abort NO-SESSION "no tmux session $SESS (the run may have finished or not started). Try: bash item6-run.sh --resume $RUN status" "Nothing changed."
  ;;
webhook)
  # No receipt id to type: the script finds the capture for THIS run (invoice id item6-<run id>), exports it, prints the one path to paste.
  F=webhook-$(date +%Y%m%d%H%M%S).json
  OUT=$("${UBU[@]}" "RD=$RDIR F=$F RID=$RID bash -s" <<'W'
set -u
umask 077
M=""; N=0
for f in $(sudo -n find /var/lib/requi-receiver/spool -maxdepth 1 -type f -name '*.json' | sort); do
  r=$(sudo -n cat "$f" | RID="$RID" python3 -c '
import sys,json,base64,os
try:
    e=json.load(sys.stdin); b=json.loads(base64.b64decode(e["raw_body_b64"]))
    ok=b.get("event_type")=="PAYMENT.CAPTURE.COMPLETED" and (b.get("resource") or {}).get("invoice_id")=="item6-"+os.environ["RID"]
    print("MATCH" if ok else "no")
except Exception: print("no")
')
  if [ "$r" = MATCH ]; then N=$((N+1)); M=$(basename "$f" .json); fi
done
echo "matches=$N"
[ "$N" = 1 ] || { echo "NOT-EXACTLY-ONE"; exit 3; }
echo "receipt=$M"
D=$(ls -d ~/item6/runs/$(basename $RD)/private/runtime-* | head -1)
[ ! -e "$D/$F" ] || { echo "file exists"; exit 1; }
sudo -n -u requi-receiver python3 /opt/requi-receiver/receiver.py export --receipt "$M" > "$D/$F"
[ -s "$D/$F" ] && [ "$(stat -c %a "$D/$F")" = 600 ] && [ "$(stat -c %u "$D/$F")" = 1000 ] || { rm -f "$D/$F"; echo "export empty or wrong mode/owner; removed"; exit 1; }
python3 - "$D/$F" <<'PY'
import json,sys
b=json.load(open(sys.argv[1])); assert b.get('format')=='item6-paypal-raw-v1'; print('envelope ok:',b['format'])
PY
W
) ; RC=$?
  echo "$OUT" | sed 's/^/   /'
  if echo "$OUT" | grep -q 'matches=0'; then abort NO-WEBHOOK-YET "no captured PayPal webhook for run $RID has arrived yet" "Nothing was changed. Finish the PayPal sandbox approval, wait ~30 seconds, and run this command again. Do NOT type anything at the harness prompt meanwhile unless it is asking for the path."; fi
  echo "$OUT" | grep -q '^receipt=' && echo "$OUT" | grep -q 'envelope ok' || abort WEBHOOK-EXPORT "could not identify and export exactly one capture for this run (see above)" "Nothing is left in use. Tell me."
  echo
  echo "################################################################"
  echo "  In the HARNESS window, at: 'Path to authentic PayPal sandbox event + transmission headers JSON:'"
  echo "  type exactly this (a file path, NOT a receipt id):"
  echo
  echo "      /private/$F"
  echo
  echo "################################################################"
  ;;
collect)
  OUTD=$DELIVERY/runs/$NAME-vps-collected-$(date +%Y%m%d%H%M%S)
  mkdir -m 700 "$OUTD"
  "${UBU[@]}" "cd $RDIR && tar cf - evidence \$(ls console-*.log watch-*.log webhook-watch-*.log 2>/dev/null)" | tar xf - -C "$OUTD" || abort COLLECT "copy failed" "Partial files may be in $OUTD; keep them."
  chmod -R go-rwx "$OUTD"; echo "collected into $OUTD:"; (cd "$OUTD" && find . -maxdepth 2 | head -30)
  ;;
go)
  echo "== final checks (read-only)"
  TMPF=$(mktemp); trap 'rm -f "$TMPF"' EXIT
  if ! "${UBU[@]}" "NAME=$NAME RID=$RID SESS=$SESS bash -s" >"$TMPF" <<'PRE'
set -u
cd ~/item6/runs/$NAME 2>/dev/null || { echo "rundir=missing"; exit 0; }
echo "rundir=ok"; echo "runvps=$([ -x run-vps.sh ] && echo yes || echo no)"
echo "evidence_empty=$([ -z "$(ls -A evidence)" ] && echo yes || echo no)"
echo "verifier=$(python3 private/verify-*.py . >/dev/null 2>&1 && echo ok || echo FAIL)"
echo "container=$(sudo -n docker ps -a --format '{{.Names}}' | grep -c "^requi-item6-$RID\$" || true)"
echo "session=$(tmux ls 2>/dev/null | grep -c "^$SESS" || true)"
echo "dockerinfo=$(sudo -n docker info >/dev/null 2>&1 && echo yes || echo no)"
echo "mem_mb=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)"
echo "listen=$(ss -ltn 2>/dev/null | grep -c '127.0.0.1:8787' || true)"
end=$(echo | timeout 10 openssl s_client -connect 15.235.101.85:443 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
echo "cert_hours=$( [ -n "$end" ] && echo $(( ($(date -d "$end" +%s) - $(date +%s)) / 3600 )) || echo -1)"
echo "cert_ok=$(echo | timeout 10 openssl s_client -connect 15.235.101.85:443 -verify_return_error 2>&1 | grep -c 'Verification: OK' || true)"
echo "now=$(date -u +%s)"
for u in api.deepseek.com api-m.sandbox.paypal.com; do echo "net_$u=$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://$u/ || echo 000)"; done
PRE
  then abort VPS-SSH "cannot ssh to ubuntu@$HOST"; fi
  PRE=$(cat "$TMPF"); g(){ printf '%s\n' "$PRE" | sed -n "s/^$1=//p" | head -1; }
  [ "$(g rundir)" = ok ] || abort NO-RUNDIR "the run directory is not on the server (run item6-ship.sh)"
  [ "$(g runvps)" = yes ] || abort NO-LAUNCHER "run-vps.sh missing"
  [ "$(g evidence_empty)" = yes ] || abort EVIDENCE-USED "evidence is not empty: a run already started here. Never re-run into it." "Use a new run directory."
  [ "$(g verifier)" = ok ] || abort VERIFIER "the compiled-config verifier fails on the server: files changed" "Do not run."
  [ "$(g container)" = 0 ] || abort CONTAINER-EXISTS "container requi-item6-$RID already exists: preserve it, never remove a paid-run container to retry"
  [ "$(g session)" = 0 ] || abort SESSION-EXISTS "tmux session $SESS already exists: use attach"
  [ "$(g dockerinfo)" = yes ] || abort VPS-DOCKER "sudo -n docker info fails"
  [ "$(g mem_mb)" -ge 2500 ] 2>/dev/null || abort VPS-MEM "only $(g mem_mb) MB available"
  [ "$(g listen)" -ge 1 ] 2>/dev/null || abort RECEIVER "webhook receiver is not listening on 127.0.0.1:8787"
  [ "$(g cert_hours)" -ge 48 ] 2>/dev/null && [ "$(g cert_ok)" -ge 1 ] 2>/dev/null || abort IP-CERT "IP certificate has $(g cert_hours)h left or does not verify publicly; PayPal would not deliver the webhook"
  sk=$(( $(g now) - $(date -u +%s) )); [ ${sk#-} -le 60 ] || abort CLOCK-SKEW "VPS clock differs by ${sk}s"
  for u in api.deepseek.com api-m.sandbox.paypal.com; do case "$(g net_$u)" in 000|"") abort "NET-$u" "$u unreachable from the VPS";; esac; done
  echo "ok  run dir, launcher, empty evidence, verifier, no old container/session, docker, ${mem:-$(g mem_mb)} MB free, receiver, IP cert $(g cert_hours)h, clock, network"
  echo "== DeepSeek balance (this Mac, key not printed)"
  BAL=$(RUN="$RUN" python3 - <<'PY'
import json,os,subprocess,sys
key=None
for line in open(os.path.join(os.environ['RUN'],'private/.env')):
    k,s,v=line.partition('=')
    if s and k=='DEEPSEEK_API_KEY':
        try: key=json.loads(v)
        except ValueError: pass
if not isinstance(key,str) or not key: print('NOKEY'); sys.exit(0)
p=subprocess.run(['curl','-sS','-m','20','-K','-','-o','-','-w','\n%{http_code}','https://api.deepseek.com/user/balance'],input='header = "Authorization: Bearer %s"\n'%key,capture_output=True,text=True)
body,_,code=p.stdout.rpartition('\n')
try:
    d=json.loads(body); usd=[b for b in d['balance_infos'] if b['currency']=='USD'][0]
    print('OK',usd['total_balance'])
except Exception: print('FAIL',code)
PY
)
  case "$BAL" in OK*) B=${BAL#OK }; echo "balance USD $B";; *) abort BALANCE "could not read the DeepSeek balance ($BAL)";; esac
  python3 -c "import sys;sys.exit(0 if float('$B')>=$CEILING else 1)" || abort BALANCE-LOW "balance USD $B is below the \$$CEILING ceiling" "Top up the DeepSeek account first."
  echo
  echo "============================================================"
  echo "THE PAID RUN. Run id: $RID"
  echo "  server: ubuntu@$HOST  run dir: ~/item6/runs/$NAME  tmux session: $SESS"
  echo "  model: deepseek-flash only; sandbox PayPal only (the 12-credit pack, $11.50 sandbox, fake money); real payments stay locked"
  echo "  spend ceiling: USD $CEILING of DeepSeek balance (run + one retry). A watcher on the server polls the real balance every 30s from the"
  echo "  start balance USD $B and stops the container (SIGINT first so the DB dump is archived, hard kill after 90s) at a drop of USD $CEILING."
  echo "  Calls in flight can overshoot slightly, and balance updates can lag. The harness has its own admission/balance checks as well."
  echo "  You will be asked at the terminal: interview answers, accept/regenerate decisions, the cost approvals, and nothing else: after your one PayPal sandbox click the run continues by itself when PayPal records the approval. The webhook file is exported by a watcher on the server. No auto answers."
  echo "  If the connection drops: bash item6-run.sh --resume $RUN attach"
  echo "============================================================"
  read -r -p "To start, type exactly:  GO $RID CEILING $CEILING   > " PHRASE
  [ "$PHRASE" = "GO $RID CEILING $CEILING" ] || abort NOT-STARTED "go phrase not typed exactly" "Nothing was started."
  echo "== starting"
  "${UBU[@]}" "NAME=$NAME RID=$RID CEILING=$CEILING START=$B bash -s" <<'INNER'
set -eu
cd ~/item6/runs/$NAME; umask 077
cat > go-inner.sh <<EOS
#!/usr/bin/env bash
cd "\$(dirname "\$0")"; umask 077
TS=\$(date +%Y%m%dT%H%M%S)
python3 watcher.py "$RID" "$CEILING" "$START" >> "watch-\$TS.log" 2>&1 &
W=\$!
bash webhook-watch.sh "$RID" >> "webhook-watch-\$TS.log" 2>&1 &
W2=\$!
script -qefc "bash run-vps.sh --go $RID" "console-\$TS.log"
RC=\$?
kill \$W \$W2 2>/dev/null || true
echo; echo "== harness exited with code \$RC. Evidence: ~/item6/runs/$NAME/evidence. Press Enter to close this window."; read -r _
EOS
cat > watcher.py <<'EOW'
import glob,json,subprocess,sys,time,urllib.request
rid,ceiling,start=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
cred=glob.glob('private/runtime-*/credentials.json')[0]; key=json.load(open(cred))['deepseek_api_key']
def bal():
    r=urllib.request.Request('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key})
    d=json.load(urllib.request.urlopen(r,timeout=20)); return float([b for b in d['balance_infos'] if b['currency']=='USD'][0]['total_balance'])
def log(m): print(time.strftime('%FT%T'),m,flush=True)
name='requi-item6-'+rid; fails=0; log('watcher start balance %.2f ceiling %.2f'%(start,ceiling))
while True:
    st=subprocess.run(['sudo','-n','docker','inspect','-f','{{.State.Status}}',name],capture_output=True,text=True).stdout.strip()
    if st in ('exited','dead'): log('container %s; watcher done'%st); break
    if st=='running':
        try:
            b=bal(); fails=0; spent=start-b; log('balance %.2f spent %.2f'%(b,spent))
            if spent>=ceiling:
                log('CEILING REACHED: sending SIGINT'); subprocess.run(['sudo','-n','docker','kill','--signal=INT',name])
                for _ in range(18):
                    time.sleep(5)
                    if subprocess.run(['sudo','-n','docker','inspect','-f','{{.State.Status}}',name],capture_output=True,text=True).stdout.strip()!='running': break
                else:
                    log('still running after 90s: hard kill'); subprocess.run(['sudo','-n','docker','kill',name])
                break
        except Exception as e:
            fails+=1; log('balance read failed (%s) %d'%(type(e).__name__,fails))
            if fails>=10: log('10 failed balance reads in a row: stopping the container (cannot enforce the ceiling)'); subprocess.run(['sudo','-n','docker','kill','--signal=INT',name]); break
    time.sleep(30)
EOW
cat > webhook-watch.sh <<'EOV'
#!/usr/bin/env bash
# Exports the PayPal sandbox capture for THIS run (invoice id item6-<run id>) into the run's private mount once it arrives.
# Exactly one match required; never overwrites; exits after one export or when the container is gone.
RID=$1; umask 077; cd "$(dirname "$0")"
D=$(ls -d private/runtime-* | head -1); done_=0
while [ $done_ = 0 ]; do
  st=$(sudo -n docker inspect -f '{{.State.Status}}' requi-item6-$RID 2>/dev/null)
  case "$st" in exited|dead) echo "$(date +%T) container $st; watcher done"; exit 0;; esac
  M=""; N=0
  for f in $(sudo -n find /var/lib/requi-receiver/spool -maxdepth 1 -type f -name '*.json' | sort); do
    r=$(sudo -n cat "$f" | RID="$RID" python3 -c '
import sys,json,base64,os
try:
    e=json.load(sys.stdin); b=json.loads(base64.b64decode(e["raw_body_b64"]))
    print("MATCH" if b.get("event_type")=="PAYMENT.CAPTURE.COMPLETED" and (b.get("resource") or {}).get("invoice_id")=="item6-"+os.environ["RID"] else "no")
except Exception: print("no")
')
    [ "$r" = MATCH ] && { N=$((N+1)); M=$(basename "$f" .json); }
  done
  if [ "$N" = 1 ]; then
    F=webhook-$(date +%Y%m%d%H%M%S).json
    sudo -n -u requi-receiver python3 /opt/requi-receiver/receiver.py export --receipt "$M" > "$D/$F"
    if [ -s "$D/$F" ] && [ "$(stat -c %a "$D/$F")" = 600 ] && [ "$(stat -c %u "$D/$F")" = 1000 ]; then echo "$(date +%T) exported capture $M to $F"; done_=1; else rm -f "$D/$F"; echo "$(date +%T) export failed; removed"; fi
  elif [ "$N" -gt 1 ]; then echo "$(date +%T) $N matching captures: refusing to choose"; exit 2
  fi
  sleep 10
done
EOV
chmod 700 go-inner.sh webhook-watch.sh; chmod 600 watcher.py
INNER
  "${UBU[@]}" -tt "tmux new-session -s $SESS 'bash ~/item6/runs/$NAME/go-inner.sh'" || true
  echo
  echo "tmux window closed or detached. Status: bash item6-run.sh --resume $RUN status ; reattach: bash item6-run.sh --resume $RUN attach"
  ;;
*) abort USAGE "mode must be go, attach, webhook, collect or status" "Nothing changed." ;;
esac
}

IMAGE=$("${UBU[@]}" 'sudo -n docker image inspect requi-item6-test --format "{{.Created}}" 2>/dev/null' || true)
case "${1:-}" in
  --resume) RUN=${2:?run dir}; MODE=${3:-attach}; ( stage_launch "$RUN" "$MODE" ); exit $? ;;
esac
REBUILD_IMG=0; [ "${1:-}" = --rebuild ] && REBUILD_IMG=1
[ -n "$IMAGE" ] || REBUILD_IMG=1
echo "Item 6 run on the Linux server ($HOST). Image on server: ${IMAGE:-none}$([ $REBUILD_IMG = 1 ] && echo '  -> will be rebuilt from your four checked-out repos (clean, on main, up to date)')"
printf '%s\n' "
This one command will, in order:
  1. $([ $REBUILD_IMG = 1 ] && echo 'build the package and the image on the server' || echo 'reuse the existing server image (pass --rebuild after merging code changes)')
  2. create a NEW run directory on this Mac with a new run id, copy your sandbox PayPal and DeepSeek values from the previous run, fetch the public price pages
  3. upload it to the server and run the dry preflight (no network, no payment, no model call)
  4. (the reviewed values print in step 2) then ask for the paid-run phrase: GO <run id> CEILING 2.00  (that typed phrase is the approval)
  5. start the interview in tmux: you answer content questions (interview answers, accept or regenerate decisions), click PayPal sandbox once (nothing to type, the run waits for PayPal's approval); the webhook file is handled automatically
  6. copy the evidence back to this Mac when it ends
Costs: DeepSeek spend capped at USD 2.00 by a balance watcher; PayPal sandbox only (12-credit pack, fake money)."
read -r -p "Type yes (English keyboard) to begin steps 1-3, anything else aborts with no change: " A
[ "$A" = yes ] || abort DECLINED "You did not type yes." "Nothing was changed."
if [ $REBUILD_IMG = 1 ]; then ( stage_build ); fi
( stage_config )
NEW=$(cat "$STATE/newdir"); [ -d "$NEW" ] || abort NO-RUN-DIR "config step did not produce a run directory"
( stage_ship "$NEW" )
( stage_launch "$NEW" go ) || true
echo "== collecting evidence"
( stage_launch "$NEW" collect ) || echo "collect failed: run  bash item6-run.sh --resume $NEW collect"
