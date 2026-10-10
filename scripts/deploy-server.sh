#!/usr/bin/env bash
set -euo pipefail
# Explicit private control-plane deployment only. No default local DB replacement.
IMAGE=${1:?usage: deploy-server.sh image@sha256:digest /path/to/reviewed.env}
ENV_FILE=${2:?reviewed environment file required}
NAME=${DEPLOY_CONTAINER_NAME:-requi-delivery}
PORT=${DEPLOY_HOST_PORT:-8000}
[[ "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo 'Immutable image digest required'; exit 1; }
[[ "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,60}$ ]] || exit 1
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<=65535)) || exit 1
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo 'Regular private environment file required'; exit 1; }
# Do not print config values or read secrets into command arguments.
python3 - "$ENV_FILE" <<'PY'
import os,stat,sys
p=sys.argv[1];s=os.stat(p)
if stat.S_IMODE(s.st_mode)&0o077:raise SystemExit('Environment file must be owner-only (chmod600)')
values={}
for line in open(p):
 line=line.strip()
 if not line or line.startswith('#'):continue
 key,sep,value=line.partition('=')
 if not sep or key in values:raise SystemExit('Malformed/duplicate environment setting')
 values[key]=value
required=('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY')
if not all(values.get(k) for k in required):raise SystemExit('Reviewed PostgreSQL/Supabase config required')
if not values['DELIVERY_DATABASE_URL'].startswith(('postgresql://','postgres://')):raise SystemExit('Cloud deployment requires PostgreSQL, no SQLite fallback')
if values.get('DELIVERY_CONTROL_PLANE_ONLY','1')!='1' or values.get('TURSO_DATABASE_URL'):raise SystemExit('Delivery-only cloud profile required')
PY
CANDIDATE="$NAME-candidate"
BACKUP="$NAME-previous"
# Never remove an earlier rollback container without an explicit operator step.
if docker inspect "$CANDIDATE" >/dev/null 2>&1 || docker inspect "$BACKUP" >/dev/null 2>&1; then
 echo 'Candidate/previous container already exists. Review it before deploying.'; exit 1
fi
docker pull "$IMAGE"
run_container() {
 docker run -d --name "$1" --restart unless-stopped --read-only \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  --env-file "$ENV_FILE" -e DELIVERY_CONTROL_PLANE_ONLY=1 -e DELIVERY_HOST=0.0.0.0 \
  -e DELIVERY_PORT=8000 "${@:2}" "$IMAGE" >/dev/null
}
healthy() {
 for attempt in {1..10}; do
  state=$(docker inspect --format '{{.State.Health.Status}}' "$1" 2>/dev/null || true)
  [[ "$state" == healthy ]] && return 0
  [[ "$state" == unhealthy ]] && return 1
  sleep 3
 done
 return 1
}
# Candidate is never published. Existing listener remains running during validation.
run_container "$CANDIDATE"
if ! healthy "$CANDIDATE"; then
 echo 'Candidate failed health. Existing service untouched; inspect candidate logs.'; exit 1
fi
docker rm -f "$CANDIDATE" >/dev/null
PREVIOUS=false
CUTOVER=false
rollback() {
 status=$?
 trap - EXIT
 if [[ "$CUTOVER" == true ]]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  if [[ "$PREVIOUS" == true ]]; then
   if docker inspect "$BACKUP" >/dev/null 2>&1; then
    docker rename "$BACKUP" "$NAME" || { echo 'Rollback rename failed; operator recovery required.'; exit 1; }
   fi
   docker start "$NAME" >/dev/null || { echo 'Previous container restart failed; operator recovery required.'; exit 1; }
   echo 'New service failed. Previous container restored; check its health.'
  else
   echo 'New service failed. No previous container existed.'
  fi
 fi
 exit "$status"
}
trap rollback EXIT
if docker inspect "$NAME" >/dev/null 2>&1; then
 PREVIOUS=true
 # Rename while running so a stop/rename failure never leaves a name collision
 # that could cause rollback to remove the original container.
 docker rename "$NAME" "$BACKUP"
 CUTOVER=true
 docker stop "$BACKUP" >/dev/null
else
 CUTOVER=true
fi
if run_container "$NAME" -p "127.0.0.1:$PORT:8000" && healthy "$NAME"; then
 CUTOVER=false
 echo 'Private delivery-only service healthy. Previous container retained; no public launch.'
 exit 0
fi
exit 1
