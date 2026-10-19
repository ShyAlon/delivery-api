#!/usr/bin/env bash
# Requi.re: put the FULL service on the server with execution switched OFF, then check it. Replaces the delivery-only container (kept stopped for rollback).
# Nothing runs a model: no provider key is used, no flag is set, no credits move. Uses the image built from current main (builds it first if it is missing).
set -euo pipefail
HOST=15.235.101.85; PORT_DEFAULT=8000; PUBLIC=https://15-235-101-85.sslip.io
. "$(dirname "$0")/lib.sh"; ops_start deploy-full "$0"; REPO=$PWD
for t in ssh curl shasum git; do command -v $t >/dev/null || abort "TOOL-$t" "$t is missing" "" "install $t"; done
[ -f scripts/docker/build-full-image.sh ] && [ -f Dockerfile.full ] || abort NO-FILE "scripts/docker/build-full-image.sh or Dockerfile.full is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
ROOT=${REQUI_REPOS_ROOT:-$(dirname "$REPO")}; TAG=""
for r in delivery-api interview-engine spec-generator verification-loop; do
  D=$ROOT/$r; [ -d "$D/.git" ] || abort "NO-$r" "no checkout at $D" "" "git clone https://github.com/ShyAlon/$r $D"
  git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "" "git -C $D fetch origin main"
  [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes to tracked files" "" "git -C $D status --short   (commit, stash or discard them)"
  if [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" != main ]; then echo "== $r: switching to main (was on $(git -C "$D" rev-parse --abbrev-ref HEAD))"; git -C "$D" checkout -q main || abort "CHECKOUT-$r" "cannot switch $r to main" "" "git -C $D branch -a"; fi
  git -C "$D" pull -q --ff-only origin main || abort "PULL-$r" "$r main cannot fast-forward to origin/main" "" "git -C $D log --oneline origin/main..main   (local commits on main?)"
  [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r is not at origin/main after pulling" "" "git -C $D status"
  TAG="${TAG:+$TAG-}$(git -C "$D" rev-parse --short=8 HEAD)"
done
IMAGE=requi-full:$TAG
WORK=$(mktemp -d); chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/remote.sh" <<'RS_EOF'
#!/usr/bin/env bash
# Runs on the server as ubuntu (sudo). Builds /etc/requi/full.env, replaces the running service with the full image (execution flag OFF), keeps the old container for rollback.
set -euo pipefail
ETC=${ETC:-/etc/requi}; DATA=${DATA:-/data/requi}; D=${DOCKER:-docker}
: "${IMAGE:?}" "${PORT:?}"
NAME=requi-delivery; BACKUP=requi-delivery-previous; STAMP=$(date -u +%Y%m%d%H%M%S)
say(){ echo "remote: $*"; }
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}; HOSTNAME_PUBLIC=${HOSTNAME_PUBLIC:-15-235-101-85.sslip.io}
# Exited containers that are not the service or its rollback copies and finished more than 2 days ago (the old item6 test containers).
stale_list(){
  local n f; for n in $(sudo $D ps -a --filter status=exited --format '{{.Names}}'); do
    case "$n" in requi-delivery|requi-delivery-previous*) continue;; esac
    f=$(sudo $D inspect --format '{{.State.FinishedAt}}' "$n" 2>/dev/null) || continue
    [ -n "$f" ] && [ "$(date -d "$f" +%s 2>/dev/null || echo 9999999999)" -lt "$(( $(date +%s) - 172800 ))" ] && echo "$n"
  done
}
caddy_state(){ if sudo test -f "$CADDYFILE"; then if sudo grep -Eq '^[[:space:]]*encode[[:space:]]' "$CADDYFILE"; then echo has-encode; elif sudo grep -q "$HOSTNAME_PUBLIC" "$CADDYFILE"; then echo missing; else echo unknown-site; fi; else echo no-file; fi; }
if [ "${MODE:-}" = list ]; then echo "stale=$(stale_list | tr '\n' ' ')"; echo "caddy=$(caddy_state)"; exit 0; fi
# 1. environment: control-plane settings + engine token + reviewed prices (if present). Flag stays off: no DELIVERY_CLOUD_EXECUTION anywhere.
sudo python3 - "$ETC" <<'PY_EOF' || { echo "remote: ENV-FAIL"; exit 3; }
import os,secrets,stat,sys
etc=sys.argv[1]
def read(name,required):
    p=os.path.join(etc,name)
    if not os.path.exists(p):
        if required:raise SystemExit(name+' is missing')
        return {}
    out={}
    for line in open(p):
        line=line.strip()
        if not line or line.startswith('#'):continue
        k,sep,v=line.partition('=')
        if not sep or not k or k in out:raise SystemExit('%s: malformed or duplicate setting %r'%(name,k[:40]))
        out[k]=v
    return out
cp=read('control-plane.env',True)
tokf=os.path.join(etc,'engine-token.env')
if not os.path.exists(tokf):
    fd=os.open(tokf+'.new',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,('INTERVIEW_ENGINE_TOKEN='+secrets.token_hex(24)+'\n').encode());os.close(fd);os.rename(tokf+'.new',tokf)
    os.system('chown deploy:deploy %s'%tokf)
tok=read('engine-token.env',True);models=read('cloud-models.env',False)
merged={}
for src in (cp,tok,models):
    for k,v in src.items():
        if k in merged:raise SystemExit('setting %s appears in two files'%k)
        merged[k]=v
merged.pop('DELIVERY_CONTROL_PLANE_ONLY',None)
merged['DELIVERY_CONTROL_PLANE_ONLY']='0'
if 'DELIVERY_CLOUD_EXECUTION' in merged:raise SystemExit('DELIVERY_CLOUD_EXECUTION is set in a settings file; this deploy keeps execution off')
for need in ('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY','INTERVIEW_ENGINE_TOKEN'):
    if not merged.get(need):raise SystemExit('%s is missing'%need)
if not merged['DELIVERY_DATABASE_URL'].startswith(('postgresql://','postgres://')):raise SystemExit('PostgreSQL is required')
path=os.path.join(etc,'full.env')
fd=os.open(path+'.new',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
os.write(fd,''.join('%s=%s\n'%(k,merged[k]) for k in sorted(merged)).encode());os.close(fd)
os.system('chown deploy:deploy %s.new && chmod 600 %s.new && mv %s.new %s'%(path,path,path,path))
print('remote: env ok: %d settings (prices file %s), execution flag absent'%(len(merged),'included' if models else 'not present'))
PY_EOF
# 2. data folder for the artifacts the container writes
sudo install -d -m 0700 "$DATA" && sudo chown 10001:10001 "$DATA"
# 3. replace the service; the old container stays (stopped) for rollback
PREV=false
if sudo $D inspect "$NAME" >/dev/null 2>&1; then
  PREV=true
  if sudo $D inspect "$BACKUP" >/dev/null 2>&1; then sudo $D rename "$BACKUP" "$BACKUP-$STAMP"; say "kept the older rollback container as $BACKUP-$STAMP"; fi
  sudo $D stop "$NAME" >/dev/null; sudo $D rename "$NAME" "$BACKUP"; say "stopped the running service (kept as $BACKUP)"
fi
rollback(){
  say "ROLLBACK: the new service did not become healthy"
  sudo $D logs --tail 30 "$NAME" 2>&1 | sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g' || true
  sudo $D rm -f "$NAME" >/dev/null 2>&1 || true
  if [ "$PREV" = true ]; then sudo $D rename "$BACKUP" "$NAME" && sudo $D start "$NAME" >/dev/null && say "previous service restored and started" || say "RESTORE FAILED: operator recovery needed (container $BACKUP)"; fi
  exit 4
}
sudo $D run -d --name "$NAME" --restart unless-stopped --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 512 --memory 3g --memory-swap 3g --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$DATA":/data \
  --env-file "$ETC/full.env" -e DELIVERY_HOST=0.0.0.0 -e DELIVERY_PORT=8000 -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || rollback
ok=false
for i in $(seq 1 50); do
  st=$(sudo $D inspect --format '{{.State.Health.Status}}' "$NAME" 2>/dev/null || true)
  [ "$st" = healthy ] && { ok=true; break; }
  [ "$st" = unhealthy ] && break
  sleep 3
done
[ "$ok" = true ] || rollback
LOG=$(sudo $D logs "$NAME" 2>&1 | sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g')
grep -q 'Cloud execution: CLOSED' <<<"$LOG" || { say "the log does not show 'Cloud execution: CLOSED'; treating as failed"; echo "$LOG" | tail -15; rollback; }
grep -q 'Cloud execution: OPEN' <<<"$LOG" && { say "execution reports OPEN; treating as failed"; rollback; }
say "healthy; $(grep 'Cloud execution:' <<<"$LOG" | tail -1 | cut -c1-200)"
say "DEPLOYED $IMAGE on 127.0.0.1:$PORT (previous container: $([ "$PREV" = true ] && echo $BACKUP || echo none))"
# 4. after the service is healthy: compression in Caddy (a failure here never touches the service), then remove the stale test containers
if [ "$(caddy_state)" = missing ]; then
  sudo cp -p "$CADDYFILE" "$CADDYFILE.bak-$STAMP"
  if sudo python3 - "$CADDYFILE" "$HOSTNAME_PUBLIC" <<'CADDY_PY'
import sys,re
p,host=sys.argv[1],sys.argv[2];lines=open(p).read().split('\n');out=[];done=False
for l in lines:
    out.append(l)
    if not done and host in l and l.rstrip().endswith('{'):
        out.append(re.match(r'\s*',l).group(0)+'\tencode zstd gzip');done=True
if not done:raise SystemExit('site block not found')
open(p,'w').write('\n'.join(out))
CADDY_PY
  then
    if sudo caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1 && sudo systemctl reload caddy; then say "Caddy compression on (backup $CADDYFILE.bak-$STAMP)"
    else sudo cp -p "$CADDYFILE.bak-$STAMP" "$CADDYFILE"; sudo systemctl reload caddy || true; say "WARNING: Caddy rejected the change; restored the previous file. The service is fine, compression is still off"; fi
  else sudo cp -p "$CADDYFILE.bak-$STAMP" "$CADDYFILE"; say "WARNING: could not edit the Caddyfile; left unchanged. Compression is still off"; fi
else say "Caddy: $(caddy_state), nothing to change"; fi
for n in $(stale_list); do sudo $D rm "$n" >/dev/null 2>&1 && say "removed stale container $n" || say "could not remove $n"; done
say "FINISHED"
RS_EOF
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes ubuntu@$HOST)
echo "== server check (read only) on ubuntu@$HOST"
FACTS=$("${SSH[@]}" 'sudo -n docker info >/dev/null && echo docker=ok
for f in control-plane.env cloud-models.env; do sudo test -f /etc/requi/$f && echo "has $f" || echo "no $f"; done
sudo docker image inspect '"$IMAGE"' >/dev/null 2>&1 && echo image=present || echo image=missing
if sudo docker inspect requi-delivery >/dev/null 2>&1; then echo "running=$(sudo docker inspect --format "{{.Config.Image}} {{.State.Status}}" requi-delivery)"; echo "port=$(sudo docker port requi-delivery 8000/tcp 2>/dev/null | head -1)"; else echo running=none; fi' 2>&1) || abort SERVER "cannot reach the server or ubuntu cannot run sudo docker: $(echo "$FACTS" | tail -2 | tr '\n' ' ')" "" "ssh ubuntu@$HOST 'sudo -n docker info' | head -3"
echo "$FACTS" | grep -q '^docker=ok$' || abort SERVER "unexpected answer from the server: $(echo "$FACTS" | tr '\n' ' ')" "" "send me this output"
echo "$FACTS" | grep -q '^has control-plane.env$' || abort NO-ENV "/etc/requi/control-plane.env is missing on the server" "" "send me this output (the delivery-only service needs it, so something is off)"
HASMODELS=$(echo "$FACTS" | grep -c '^has cloud-models.env$' || true)
RUNNING=$(echo "$FACTS" | sed -n 's/^running=//p'); PORT=$(echo "$FACTS" | sed -n 's/^port=.*:\([0-9][0-9]*\)$/\1/p'); PORT=${PORT:-$PORT_DEFAULT}
LIST=$("${SSH[@]}" "MODE=list IMAGE=$IMAGE PORT=$PORT bash -s" < "$WORK/remote.sh" 2>&1) || abort SERVER "the read-only server check failed: $(echo "$LIST" | tail -2 | tr '\n' ' ')" "" "send me this output"
STALE=$(echo "$LIST" | sed -n 's/^stale=//p'); CADDY=$(echo "$LIST" | sed -n 's/^caddy=//p')
if [ "$(echo "$FACTS" | sed -n 's/^image=//p')" = missing ]; then
  echo "image $IMAGE is not on the server yet; running the image build first (it asks its own yes)"
  bash scripts/docker/build-full-image.sh || abort IMAGE-BUILD "the image build did not finish" "The server service was not touched." "send me the output above"
fi
echo
echo "PLAN"
echo "  image            $IMAGE (current main of the four repos)"
echo "  replaces         ${RUNNING:-nothing running} (kept stopped as requi-delivery-previous for rollback)"
echo "  listens on       127.0.0.1:$PORT behind Caddy at $PUBLIC (unchanged)"
echo "  settings file    /etc/requi/full.env, built from control-plane.env + an engine token (made once) + $([ "$HASMODELS" = 1 ] && echo cloud-models.env || echo 'no price file yet')"
echo "  execution        OFF. Users can sign in and use the screens; starting work answers 'closed'. No provider key, no spend."
echo "  limits           3 GB memory, 512 processes, read-only container, artifacts in /data/requi"
case "$CADDY" in missing) echo "  compression      adds 'encode zstd gzip' to the Caddy site (backup kept, validated first, restored if Caddy rejects it)";; has-encode) echo "  compression      already on";; *) echo "  compression      not changed (Caddyfile state: $CADDY)";; esac
echo "  cleanup          removes exited test containers older than 2 days: ${STALE:-none}"
read -r -p "Type yes (English keyboard) to deploy, anything else changes nothing: " A
[ "$A" = yes ] || abort DECLINED "You did not type yes." "Nothing was changed." "nothing needed"
echo "== deploying"
RC=0; "${SSH[@]}" "IMAGE=$IMAGE PORT=$PORT bash -s" < "$WORK/remote.sh" 2>&1 | tee /tmp/requi-deploy-full.log || RC=$?
SAVED=$(grep -c 'remote: DEPLOYED' /tmp/requi-deploy-full.log || true)
[ "$SAVED" = 1 ] || abort DEPLOY "the server did not finish the deploy (see the lines above; log /tmp/requi-deploy-full.log)" "If it says ROLLBACK, the previous service was restored and started." "send me this output"
grep -q 'remote: FINISHED' /tmp/requi-deploy-full.log || echo "NOTE: the service is deployed, but the compression or cleanup step did not finish; see the lines above."
echo "== public checks from this Mac"
H=$(curl -sS -m 15 "$PUBLIC/health" 2>&1) || abort PUBLIC-HEALTH "cannot read $PUBLIC/health: $H" "The service is deployed on the server; only the public check failed." "curl -sS $PUBLIC/health   (send me this output)"
echo "health: $H"
echo "$H" | grep -Eq '"ok": *true' || abort PUBLIC-HEALTH "health did not say ok" "" "send me this output"
if echo "$H" | grep -q control_plane_only; then abort PUBLIC-PROFILE "the public service still reports the delivery-only profile" "" "send me this output"; fi
CODE=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$PUBLIC/" 2>&1) || CODE=fail
[ "$CODE" = 200 ] || abort PUBLIC-UI "the home page answered $CODE, not 200" "" "send me this output"
echo "home page: 200"
echo "compression: $(curl -sSI -m 15 -H 'Accept-Encoding: gzip' "$PUBLIC/assets/task.js" 2>/dev/null | tr -d '\r' | grep -i '^content-encoding' || echo 'none')"
echo "assets cache: $(curl -sSI -m 15 "$PUBLIC/assets/task.js" 2>/dev/null | tr -d '\r' | grep -i '^cache-control' || echo 'unknown')   (no-cache means the new web build is live)"
echo "request ref header: $(curl -sSI -m 15 "$PUBLIC/health" 2>/dev/null | tr -d '\r' | grep -i '^x-request-id' || echo 'missing')"
echo
echo "DONE. Full service running on the server, execution OFF, image $IMAGE."
echo "Now, in your browser: open $PUBLIC , sign in, and look at the screens. Tell me what you see."
