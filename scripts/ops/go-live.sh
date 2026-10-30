#!/usr/bin/env bash
# Requi.re go-live (steps 1 and 2): re-read the DeepSeek price and limit pages and show the numbers; after one yes write the reviewed prices and ceiling and your DeepSeek key into /etc/requi on the server; after a second yes restart the service on the same image with execution ON. No model call, no spend: a person still needs an admission row and credits (credits script). Rolls back to the OFF service by itself if it does not start healthy with Cloud execution: OPEN.
set -euo pipefail
HOST=15.235.101.85
REMOTE_FILE=/etc/requi/cloud-models.env
OPS_NOTE="Nothing was written on the server."
. "$(dirname "$0")/lib.sh"; ops_start go-live "$0" --venv; REPO=$PWD; ops_settings
for f in src/delivery_api/vendor_sources.py src/delivery_api/production_gateway.py src/delivery_api/retail_pricing.py; do [ -f "$f" ] || abort NO-FILE "$f is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"; done
command -v ssh >/dev/null || abort NO-SSH "ssh is not installed" "" "install OpenSSH"
command -v shasum >/dev/null || abort NO-SHASUM "shasum is not installed" "" "install it (it ships with macOS)"
UBU=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes ubuntu@$HOST)
WORK=$(mktemp -d); chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT
cp "$(dirname "$0")/pin.py" "$WORK/pin.py"
echo "== reading the two DeepSeek pages (public GET, nothing paid)"
rc=0; PYTHONPATH=src .venv/bin/python "$WORK/pin.py" "$WORK" > "$WORK/out.txt" 2>&1 || rc=$?
if [ "$rc" != 0 ]; then
  cat "$WORK/out.txt" | tail -15
  case "$(tail -1 "$WORK/out.txt")" in PIN-FAIL*) abort PIN "see the line above" "" "send me this output; the page or the parser changed, and I have to look before anything is pinned";; *) abort PIN-CRASH "the pin step crashed" "" "send me this whole output";; esac
fi
cat "$WORK/out.txt"
echo
echo "== server check (read only) on ubuntu@$HOST"
PRE=$("${UBU[@]}" "sudo -n true && id -u deploy >/dev/null && test -d /etc/requi && sudo docker inspect requi-delivery >/dev/null && sudo test -f /etc/requi/full.env && sudo test -f /etc/requi/engine-token.env && sudo test -f /etc/requi/control-plane.env && echo ok; test -e $REMOTE_FILE && echo existing || echo new" 2>&1) || abort SERVER "cannot reach the server or /etc/requi, deploy user or sudo is missing: $(echo "$PRE" | tail -2 | tr '\n' ' ')" "" "ssh ubuntu@$HOST 'ls -ld /etc/requi; id deploy; sudo -n true'   (send me this output)"
echo "$PRE" | grep -q '^ok$' || abort SERVER "unexpected answer from the server: $(echo "$PRE" | tr '\n' ' ')" "" "send me this output"
echo "ok  server ready; $REMOTE_FILE is $(echo "$PRE" | tail -1)"
echo
echo "== DeepSeek key"
HAVEKEY=$("${UBU[@]}" "sudo test -s /etc/requi/deepseek.env && echo yes || echo no")
KEYFILE="$WORK/deepseek.env"; NEWKEY=no
KEY=${DEEPSEEK_API_KEY:-}
if [ -z "$KEY" ] && [ "$HAVEKEY" != yes ]; then
  printf 'DEEPSEEK_API_KEY missing locally and on server (hidden): '; read -rs KEY < /dev/tty; echo
fi
if [ -n "$KEY" ]; then
  case "$KEY" in *[[:space:]]*) abort KEY "the key has a space or line break in it" "" "run this command again and enter only the key";; esac
  [ "${#KEY}" -ge 20 ] || abort KEY "that is too short to be a DeepSeek key" "" "run this command again and paste the whole key"
  printf 'DEEPSEEK_API_KEY=%s\n' "$KEY" > "$KEYFILE"; chmod 600 "$KEYFILE"; NEWKEY=yes
elif [ "$HAVEKEY" != yes ]; then abort KEY "no key given and the server has none" "" "run this command again and save the key in preview.env or .env"; fi
unset KEY
echo
echo "STEP 1 of 2 - write settings (nothing restarts, execution stays OFF)"
echo "  prices + ceiling  -> $REMOTE_FILE (shown above; the old copy kept as .prev)"
echo "  DeepSeek key      -> /etc/requi/deepseek.env ($([ "$NEWKEY" = yes ] && echo 'the locally loaded key' || echo 'kept as it is'); owner deploy, mode 600)"
read -r -p "Type yes (English keyboard) to write these, anything else changes nothing: " A
[ "$A" = yes ] || abort DECLINED "You did not type yes." "Nothing was written." "nothing needed"
EV=$HOME/requi-evidence/price-pin-$(date +%Y%m%d-%H%M%S); mkdir -p "$EV"; chmod 700 "$EV"
cp "$WORK"/cloud-models.env "$WORK"/page-*.html "$WORK/out.txt" "$EV"/
LOCAL=$(shasum -a 256 "$WORK/cloud-models.env" | cut -d' ' -f1)
"${UBU[@]}" "sudo sh -c 'umask 077; set -e; [ ! -e $REMOTE_FILE ] || cp -p $REMOTE_FILE $REMOTE_FILE.prev; cat > $REMOTE_FILE.new; chown deploy:deploy $REMOTE_FILE.new; chmod 600 $REMOTE_FILE.new; mv $REMOTE_FILE.new $REMOTE_FILE'" < "$WORK/cloud-models.env" || abort WRITE "the write failed part way; the old file is still in place or at $REMOTE_FILE.prev" "Check the server file before the next run." "ssh ubuntu@$HOST 'ls -l /etc/requi'   (send me this output)"
if [ "$NEWKEY" = yes ]; then
  "${UBU[@]}" "sudo sh -c 'umask 077; set -e; K=/etc/requi/deepseek.env; [ ! -e \$K ] || cp -p \$K \$K.prev; cat > \$K.new; chown deploy:deploy \$K.new; chmod 600 \$K.new; mv \$K.new \$K'" < "$KEYFILE" || abort WRITE "the key file write failed part way" "Check it before the next run." "ssh ubuntu@$HOST 'sudo ls -l /etc/requi'   (send me this output)"
fi
rm -f "$KEYFILE"
REMOTE=$("${UBU[@]}" "sudo sha256sum $REMOTE_FILE | cut -d' ' -f1; sudo stat -c '%U %a' $REMOTE_FILE; sudo stat -c '%U %a' /etc/requi/deepseek.env; sudo grep -c '^DEEPSEEK_API_KEY=.' /etc/requi/deepseek.env") || abort VERIFY "cannot read the files back" "They may be written; check them." "ssh ubuntu@$HOST 'sudo ls -l /etc/requi'   (send me this output)"
[ "$(echo "$REMOTE" | sed -n 1p)" = "$LOCAL" ] || abort VERIFY "the server price file differs from what was shown" "Do not turn execution on." "send me this output"
[ "$(echo "$REMOTE" | sed -n 2p)" = "deploy 600" ] && [ "$(echo "$REMOTE" | sed -n 3p)" = "deploy 600" ] || abort VERIFY "a server file is not deploy:600" "Do not turn execution on." "send me this output"
[ "$(echo "$REMOTE" | sed -n 4p)" = 1 ] || abort VERIFY "the key file does not hold exactly one key line" "Do not turn execution on." "send me this output"
echo "ok  price file matches ($LOCAL); key file present; both owner deploy, mode 600. Evidence on this Mac: $EV"
echo
echo "STEP 2 of 2 - turn execution ON"
echo "  What changes: the running service restarts on the SAME image with the key, the prices and DELIVERY_CLOUD_EXECUTION=1."
echo "  What this does NOT do: no model call and no spend. A call needs, for each person, an admission row and prepaid credits. A person without both is answered 'not admitted'."
echo "  Safety: the service must start healthy and log 'Cloud execution: OPEN', otherwise the previous (OFF) service is put back automatically."
read -r -p "Type yes (English keyboard) to turn execution ON now, anything else leaves it OFF with the settings written: " A
[ "$A" = yes ] || { echo; echo "Stopped. Settings are written, execution is still OFF. Run this command again to finish."; exit 0; }
cat > "$WORK/on.sh" <<'RS_EOF'
#!/usr/bin/env bash
set -euo pipefail
ETC=${ETC:-/etc/requi}; DATA=${DATA:-/data/requi}; NAME=requi-delivery; BACKUP=requi-delivery-previous; STAMP=$(date -u +%Y%m%d%H%M%S)
say(){ echo "remote: $*"; }
sudo docker inspect "$NAME" >/dev/null 2>&1 || { say "NO-SERVICE: no running service to replace"; exit 3; }
IMAGE=$(sudo docker inspect --format '{{.Config.Image}}' "$NAME"); PORT=$(sudo docker port "$NAME" 8000/tcp | head -1 | sed 's/.*://')
[ -n "$IMAGE" ] && [ -n "$PORT" ] || { say "NO-SERVICE: cannot read the image or port"; exit 3; }
sudo cp -p "$ETC/full.env" "$ETC/full.env.pre-on"
sudo python3 - "$ETC" <<'PY_EOF' || { echo "remote: ENV-FAIL"; exit 3; }
import os,sys
etc=sys.argv[1]
def read(name):
    out={}
    for line in open(os.path.join(etc,name)):
        line=line.strip()
        if not line or line.startswith('#'):continue
        k,sep,v=line.partition('=')
        if not sep or not k or k in out:raise SystemExit('%s: malformed or duplicate setting %r'%(name,k[:40]))
        out[k]=v
    return out
merged={}
for name in ('control-plane.env','engine-token.env','cloud-models.env','deepseek.env'):
    for k,v in read(name).items():
        if k in merged:raise SystemExit('setting %s appears in two files'%k)
        merged[k]=v
if 'DELIVERY_CLOUD_EXECUTION' in merged:raise SystemExit('DELIVERY_CLOUD_EXECUTION is set in a settings file')
merged['DELIVERY_CONTROL_PLANE_ONLY']='0';merged['DELIVERY_CLOUD_EXECUTION']='1'
for need in ('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY','INTERVIEW_ENGINE_TOKEN','DEEPSEEK_API_KEY','VMODEL_REVIEWED_MODEL_PRICES','VMODEL_REVIEWED_CONTEXT_CEILING'):
    if not merged.get(need):raise SystemExit('%s is missing'%need)
path=os.path.join(etc,'full.env')
fd=os.open(path+'.new',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
os.write(fd,''.join('%s=%s\n'%(k,merged[k]) for k in sorted(merged)).encode());os.close(fd)
os.system('chown deploy:deploy %s.new && chmod 600 %s.new && mv %s.new %s'%(path,path,path,path))
print('remote: env ok: %d settings, execution flag set'%len(merged))
PY_EOF
if sudo docker inspect "$BACKUP" >/dev/null 2>&1; then sudo docker rename "$BACKUP" "$BACKUP-$STAMP"; say "kept the older rollback container as $BACKUP-$STAMP"; fi
sudo docker stop "$NAME" >/dev/null; sudo docker rename "$NAME" "$BACKUP"; say "stopped the OFF service (kept as $BACKUP)"
rollback(){
  say "ROLLBACK: the service with execution ON did not start as expected"
  sudo docker logs --tail 30 "$NAME" 2>&1 | sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g; s#sk-[A-Za-z0-9_-]+#sk-[hidden]#g' || true
  sudo docker rm -f "$NAME" >/dev/null 2>&1 || true
  sudo cp -p "$ETC/full.env.pre-on" "$ETC/full.env" || true
  sudo docker rename "$BACKUP" "$NAME" && sudo docker start "$NAME" >/dev/null && say "previous service (execution OFF) restored and started" || say "RESTORE FAILED: operator recovery needed (container $BACKUP)"
  exit 4
}
sudo docker run -d --name "$NAME" --restart unless-stopped --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 512 --memory 3g --memory-swap 3g --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$DATA":/data \
  --env-file "$ETC/full.env" -e DELIVERY_HOST=0.0.0.0 -e DELIVERY_PORT=8000 -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || rollback
ok=false
for i in $(seq 1 50); do
  st=$(sudo docker inspect --format '{{.State.Health.Status}}' "$NAME" 2>/dev/null || true)
  [ "$st" = healthy ] && { ok=true; break; }
  [ "$st" = unhealthy ] && break
  sleep 3
done
[ "$ok" = true ] || rollback
LOG=$(sudo docker logs "$NAME" 2>&1 | sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g; s#sk-[A-Za-z0-9_-]+#sk-[hidden]#g')
grep -q 'Cloud execution: OPEN' <<<"$LOG" || { say "the log does not show 'Cloud execution: OPEN'"; grep 'Cloud execution:' <<<"$LOG" | tail -3 | cut -c1-400; rollback; }
say "$(grep 'Cloud execution:' <<<"$LOG" | tail -1 | cut -c1-200)"
say "ON-DONE $IMAGE on 127.0.0.1:$PORT (previous OFF container kept as $BACKUP)"
RS_EOF
RC=0; "${UBU[@]}" "bash -s" < "$WORK/on.sh" 2>&1 | tee /tmp/requi-go-live.log || RC=$?
grep -q 'remote: ON-DONE' /tmp/requi-go-live.log || abort ON "the server did not finish turning execution on (see the lines above; log /tmp/requi-go-live.log)" "If it says ROLLBACK, the previous OFF service was restored and started." "send me this output"
PUBLIC=https://15-235-101-85.sslip.io
H=$(curl -sS -m 15 "$PUBLIC/health" 2>&1) || abort PUBLIC-HEALTH "cannot read $PUBLIC/health: $H" "Execution is ON on the server; only the public check failed." "curl -sS $PUBLIC/health   (send me this output)"
echo "health: $H"
echo "$H" | grep -Eq '"ok": *true' || abort PUBLIC-HEALTH "health did not say ok" "" "send me this output"
echo
echo "DONE. Prices pinned, key stored, execution ON (spend is possible only for a person with an admission row and prepaid credits). Undo: ssh ubuntu@15.235.101.85 'sudo docker stop requi-delivery && sudo docker rename requi-delivery requi-delivery-on && sudo docker rename requi-delivery-previous requi-delivery && sudo docker start requi-delivery'"
