#!/usr/bin/env bash
# Requi.re local preview: run the candidate server image on this Mac, against the LIVE database, with execution OFF.
# Default (fully local): the image is built ON THIS MAC from the four checkouts and the settings are read from the file
# saved on this Mac by scripts/ops/preview-provision.sh (a one-time step). No server access, nothing is copied from the
# server; artifact files, phase output files and interview transcripts are not part of a local preview, and the app says
# so where they would appear. PREVIEW_VIA_SERVER=1 keeps the server flow: the image is built on the server and the data
# folder is copied here (read only on the server). Writes on this Mac: a private temp folder (settings copy), one Docker
# image and one container. The database is the real one: signing in and every button that writes (Grant Credits,
# Admin Stop, delete task, stop run) is a real write. No model key is passed and execution is forced off, so a paid run
# is not possible. Ctrl-C removes the container and the temp folder.
set -euo pipefail
HOST=${REQUI_SERVER:-ubuntu@15.235.101.85}; DATA=${REQUI_DATA:-/data/requi}; PORT=${PREVIEW_PORT:-8000}; MAX_MB=${PREVIEW_MAX_MB:-2000}
NAME=requi-preview; WORK=""; STARTED=0

# Settings for the preview: ONLY the app's own DELIVERY_* settings are kept (allowlist), minus anything that names a model provider or an API key,
# then the forced values last. The sign-in origin is the preview's own address, 127.0.0.1 with its port, plus exactly localhost with the same port (the live one would make sign-in reject the browser). Reads $1, writes $2 (mode 600, created that way). Prints nothing.
build_env(){
  ( umask 077; : > "$2"; chmod 600 "$2"
    grep -E '^DELIVERY_[A-Z0-9_]*=' "$1" | grep -v -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|MISTRAL|GROQ|COHERE|XAI|PROVIDER|API_KEY|MODEL_KEY)[A-Z0-9_]*=' \
      | grep -v -E '^(DELIVERY_CLOUD_EXECUTION|DELIVERY_COOKIE_SECURE|DELIVERY_PREVIEW|DELIVERY_LOCAL_PREVIEW|DELIVERY_METRICS_DB|DELIVERY_HOST|DELIVERY_PORT|DELIVERY_CONTROL_PLANE_ONLY|DELIVERY_PUBLIC_ORIGIN|DELIVERY_ALT_ORIGINS)=' >> "$2" || true
    # The engine token is a password between the two processes inside the preview container only. It is made fresh here (never the live one, which the allowlist drops).
    tok=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); [ ${#tok} = 48 ] || exit 1
    printf '%s\n' "INTERVIEW_ENGINE_TOKEN=$tok" DELIVERY_CONTROL_PLANE_ONLY=0 DELIVERY_CLOUD_EXECUTION=0 DELIVERY_COOKIE_SECURE=0 DELIVERY_PREVIEW=1 DELIVERY_METRICS_DB=/tmp/run_metrics.db DELIVERY_HOST=0.0.0.0 DELIVERY_PORT=8000 "DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT" "DELIVERY_ALT_ORIGINS=http://localhost:$PORT" >> "$2" ) || return 1
  chmod 600 "$2"
}
# Hide connection strings in anything printed.
scrub(){ sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g'; }
cleanup(){
  rc=$?; trap - EXIT INT TERM
  if [ "$STARTED" = 1 ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
  [ -z "$WORK" ] || rm -rf "$WORK"
  [ "$STARTED" != 1 ] || echo "== preview stopped: container $NAME removed, temp folder deleted (the image stays, it holds no settings)"
  exit $rc
}

# The four checkouts at origin/main, exactly as deploy-full takes them. Sets TAG, REPOS, IMAGE.
candidate_tag(){
  ROOT=${REQUI_REPOS_ROOT:-$(dirname "$REPO")}; TAG=""; REPOS=""
  for r in delivery-api interview-engine spec-generator verification-loop; do
    D=$ROOT/$r; [ -d "$D/.git" ] || abort "NO-$r" "no checkout at $D" "" "git clone https://github.com/ShyAlon/$r $D"
    git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "" "git -C $D fetch origin main"
    [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes to tracked files" "" "git -C $D status --short   (commit, stash or discard them)"
    [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" = main ] || abort "BRANCH-$r" "$r is not on main" "" "git -C $D checkout main"
    git -C "$D" pull -q --ff-only origin main || abort "PULL-$r" "$r main cannot fast-forward to origin/main" "" "git -C $D log --oneline origin/main..main"
    [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r is not at origin/main after pulling" "" "git -C $D status"
    S=$(git -C "$D" rev-parse --short=8 HEAD); TAG="${TAG:+$TAG-}$S"; REPOS="${REPOS:+$REPOS,}$r=$S"
  done
  IMAGE=requi-full:$TAG
}

local_flow(){
  OPS_NOTE="Nothing was started and the database was not touched. A built image may remain on this Mac."
  for t in docker curl git shasum tar; do command -v $t >/dev/null 2>&1 || abort "TOOL-$t" "$t is missing" "" "install $t (Docker: open Docker Desktop once, or brew install --cask docker)"; done
  docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"
  case "$PORT" in ''|*[!0-9]*) abort PORT "PREVIEW_PORT is not a number" "" "unset PREVIEW_PORT";; esac
  if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then abort PORT-BUSY "something already listens on 127.0.0.1:$PORT" "" "stop it, or: PREVIEW_PORT=8010 ops preview"; fi
  if [ -n "${PREVIEW_CHECK_SESSION:-}" ]; then abort LOCAL-NO-CHK "PREVIEW_CHECK_SESSION compares a session file on the server against its copy; the local preview has no server access" "" "unset PREVIEW_CHECK_SESSION   (or PREVIEW_VIA_SERVER=1 ops preview for the server flow, which needs SSH)"; fi
  docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && abort LEFTOVER "a container named $NAME is left over from an earlier preview" "" "docker rm -f $NAME"

  # 1. the candidate: the four checkouts at origin/main, exactly as deploy-full takes them
  [ -f scripts/docker/build-local-image.sh ] || abort NO-FILE "scripts/docker/build-local-image.sh is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
  candidate_tag
  MACHINE=$(uname -m); case "$MACHINE" in arm64|aarch64) NATIVE=arm64;; x86_64|amd64) NATIVE=amd64;; *) abort ARCH "unknown machine architecture $MACHINE";; esac
  EXPECTED_ARCH=${PREVIEW_ARCH:-$NATIVE}; case "$EXPECTED_ARCH" in arm64|amd64) ;; *) abort ARCH "PREVIEW_ARCH must be arm64 or amd64, not '$EXPECTED_ARCH'" "" "unset PREVIEW_ARCH";; esac
  PLATFORM=linux/$EXPECTED_ARCH

  # 2. the settings saved on this Mac by ops preview-provision (one time), never printed
  ops_settings DELIVERY_DATABASE_URL,DELIVERY_SUPABASE_URL,DELIVERY_SUPABASE_ANON_KEY

  echo "ops preview: candidate $IMAGE (built on this Mac, no server access)"
  echo "  What it does: builds/uses $IMAGE on this Mac from the four checkouts, reads the settings saved on this Mac"
  echo "  (ops preview-provision), and runs the image on http://127.0.0.1:$PORT with execution OFF and no model key."
  echo "  The database is the REAL one: signing in and any button that writes (Grant Credits, Admin Stop, delete task, stop run) is a real change."
  echo "  Not part of a local preview: artifact files, phase output files and interview transcripts - the app says so where they would appear."
  echo "  The image is this Mac's architecture ($EXPECTED_ARCH); the deployed server build is amd64. Ctrl-C removes the container and the temp folder."
  printf 'Type yes (English keyboard) to start, anything else changes nothing: '
  read -r A < "${PREVIEW_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "" "run ops preview again and type yes"

  trap cleanup EXIT INT TERM
  umask 077; WORK=$(mktemp -d "${TMPDIR:-/tmp}/requi-preview.XXXXXXXXXX"); chmod 700 "$WORK"; mkdir "$WORK/data"

  # 3. settings: saved file to a private temp file, never printed, execution off, local-preview flag on
  .venv/bin/python scripts/ops/settings.py delivery-env "$WORK/loaded.env" || abort SETTINGS "cannot prepare private settings"
  build_env "$WORK/loaded.env" "$WORK/preview.env" || abort NO-TOKEN "could not make the temporary engine token" "" "send me this output"
  printf 'REQUI_EXPECTED_REPOS=%s\n' "$REPOS" >> "$WORK/preview.env"
  printf 'DELIVERY_LOCAL_PREVIEW=1\n' >> "$WORK/preview.env"; chmod 600 "$WORK/preview.env"
  grep -q -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|API_KEY)' "$WORK/preview.env" && abort MODEL-KEY "a model key is still in the preview settings" "" "send me this output (not the file)"
  [ "$(grep -c '^DELIVERY_CLOUD_EXECUTION=' "$WORK/preview.env")" = 1 ] && grep -q '^DELIVERY_CLOUD_EXECUTION=0$' "$WORK/preview.env" || abort EXEC-ON "execution is not forced off in the preview settings" "" "send me this output"
  [ "$(grep -c '^DELIVERY_PUBLIC_ORIGIN=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's sign-in address is not set to its own local address" "" "send me this output"
  [ "$(grep -c '^DELIVERY_ALT_ORIGINS=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_ALT_ORIGINS=http://localhost:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's second sign-in address is not exactly localhost with its own port" "" "send me this output"
  [ "$(grep -c '^DELIVERY_LOCAL_PREVIEW=1$' "$WORK/preview.env")" = 1 ] || abort LOCAL-FLAG "the local-preview flag is not set exactly once in the preview settings" "" "send me this output"

  # 4. the image on this Mac, built here and smoke checked
  HAVE=1; docker image inspect "$IMAGE" >/dev/null 2>&1 || HAVE=0
  if [ "$HAVE" = 1 ]; then
    echo "== the image is already on this Mac: checking that it passes the smoke check before it is reused"
    if docker run --rm --platform "$PLATFORM" --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 "$IMAGE" python /app/full-smoke.py; then
      echo "== reuse smoke passed"
    elif docker rmi "$IMAGE" >/dev/null 2>&1; then
      HAVE=0; echo "== that image failed and its tag was removed (that exact tag only); building it again"
    else
      abort IMAGE-SMOKE "the existing image failed the smoke check and could not be removed" "" "send me the output above"
    fi
  fi
  if [ "$HAVE" = 0 ]; then
    echo "== building $IMAGE on this Mac (build-local-image.sh asks its own yes; it publishes the tag only after the smoke check passes)"
    ( umask 022; bash scripts/docker/build-local-image.sh ) || abort BUILD "the local build did not finish" "Nothing was started on this Mac. The build script removes a candidate image that fails its smoke check." "send me this output"
  fi
  docker image inspect "$IMAGE" >/dev/null 2>&1 || abort IMAGE-MISSING "the image $IMAGE is not on this Mac after the build" "" "docker image ls requi-full   (send me this output)"
  ARCH=$(docker image inspect --format '{{.Architecture}}' "$IMAGE" 2>/dev/null || true)
  [ "$ARCH" = "$EXPECTED_ARCH" ] || abort ARCH "the image is $ARCH, expected $EXPECTED_ARCH (this Mac's local build)" "" "send me this output"

  # 5. can this Mac reach the database? (prints only OK or the error type, never the address)
  DBCHECK='import os,sys
try:
    import psycopg
    psycopg.connect(os.environ["DELIVERY_DATABASE_URL"],connect_timeout=10).execute("select 1")
    print("DBOK")
except Exception as e:
    print("DBFAIL",type(e).__name__)'
  OUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$DBCHECK" 2>&1 | scrub | tail -1 || true)
  [ "$OUT" = DBOK ] || abort NO-DB "this Mac cannot open the database ($OUT)" "" "check your network or VPN; if it says OperationalError the database may not accept this Mac's address: send me this line"

  # 5b. the image's OWN startup rules, run on these exact settings. Prints problem names only, never a value.
  CONTRACT='import os,sys;sys.path.insert(0,"/app");import full_entry;p=full_entry.check_env(os.environ);print("CONTRACT-OK" if not p else "CONTRACT-PROBLEMS");[print("problem:",x) for x in p]'
  COUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$CONTRACT" 2>&1 | scrub | tail -8 || true)
  echo "$COUT" | grep -qx CONTRACT-OK || abort NOT-STARTABLE "the image would not start with these settings: $(echo "$COUT" | grep '^problem:' | tr '\n' ' ')" "" "send me this output"

  # 6. run it (the data folder is an empty local one: no server copy in a local preview)
  docker run -d --name "$NAME" --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g --memory-swap 2g \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$WORK/data":/data --env-file "$WORK/preview.env" -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || abort RUN "the container did not start" "" "docker logs $NAME   (send me this output)"
  STARTED=1
  OK=0
  for i in $(seq 1 "${PREVIEW_WAIT_TRIES:-90}"); do
    if curl -fs --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then OK=1; break; fi
    docker ps --format '{{.Names}}' | grep -qx "$NAME" || break
    sleep "${PREVIEW_POLL:-2}"
  done
  if [ "$OK" != 1 ]; then echo "== the container did not become healthy; last log lines:"; docker logs --tail 30 "$NAME" 2>&1 | scrub; abort NOT-HEALTHY "the preview did not start" "" "send me the log lines above"; fi
  # readiness needs POSITIVE evidence, and anything missing or unknown fails closed:
  # (1) the app's own startup line says CLOSED and never OPEN, (2) the running container's own environment says execution is 0 and holds no provider key.
  LOGS=$(docker logs "$NAME" 2>&1 | scrub)
  echo "$LOGS" | grep -q 'Cloud execution: CLOSED' || abort EXEC-UNKNOWN "the app did not report 'Cloud execution: CLOSED' at startup" "" "send me this output"
  ! echo "$LOGS" | grep -q 'Cloud execution: OPEN' || abort EXEC-OPEN "the app reports execution OPEN" "" "send me this output"
  PROBE='import os,re;print("EXEC="+os.environ.get("DELIVERY_CLOUD_EXECUTION","unset"));print("KEYS="+str(len([k for k in os.environ if re.search("DEEPSEEK|OPENAI|ANTHROPIC|API_KEY",k,re.I)])))'
  EXECOUT=$(docker exec "$NAME" python -c "$PROBE" 2>/dev/null || true)
  echo "$EXECOUT" | grep -qx 'EXEC=0' && echo "$EXECOUT" | grep -qx 'KEYS=0' || abort EXEC-UNVERIFIED "the running container's environment does not confirm execution off and no provider key" "" "send me this output"
  echo
  echo "PREVIEW READY: http://127.0.0.1:$PORT   (execution CLOSED, no model key, real database, real sign-in)"
  echo "Open it as http://127.0.0.1:$PORT or http://localhost:$PORT: sign-in accepts only these two addresses. Each address keeps its own sign-in session."
  echo "Running build: $(printf '%s' "$REPOS" | sed 's/=/ /g; s/,/, /g')"
  echo "Local preview: built on this Mac ($EXPECTED_ARCH; the deployed server build is amd64), no server access."
  echo "Not part of a local preview: artifact files, phase output files and interview transcripts - the app says so where they would appear."
  echo "Warning: Grant Credits, Admin Stop, delete task and stop run change REAL data."
  echo "Press Ctrl-C here when you are done."
  docker logs -f "$NAME" 2>&1 | scrub || true
}

server_flow(){
  OPS_NOTE="Nothing was started and the database was not touched. A built image may remain on the server unless the output says it was removed."
  for t in docker ssh curl git shasum tar; do command -v $t >/dev/null 2>&1 || abort "TOOL-$t" "$t is missing" "" "install $t (Docker: open Docker Desktop once, or brew install --cask docker)"; done
  docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"
  case "$PORT" in ''|*[!0-9]*) abort PORT "PREVIEW_PORT is not a number" "" "unset PREVIEW_PORT";; esac
  if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then abort PORT-BUSY "something already listens on 127.0.0.1:$PORT" "" "stop it, or: PREVIEW_PORT=8010 ops preview"; fi
  CHK="${PREVIEW_CHECK_SESSION:-}"
  if [ -n "$CHK" ]; then
    case "$CHK" in .*|*..*|*[!A-Za-z0-9._-]*) abort CHECK-ID "PREVIEW_CHECK_SESSION is not a plain session id (letters, digits, dot, dash, underscore; no leading dot, no ..)" "" "PREVIEW_CHECK_SESSION=<the session id from the Debug dialog> ops preview";; esac
    [ ${#CHK} -le 80 ] || abort CHECK-ID "PREVIEW_CHECK_SESSION is too long to be a session id" "" "PREVIEW_CHECK_SESSION=<the session id from the Debug dialog> ops preview"
  fi
  docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && abort LEFTOVER "a container named $NAME is left over from an earlier preview" "" "docker rm -f $NAME"

  # 1. the candidate: the four checkouts at origin/main, exactly as deploy-full takes them
  [ -f scripts/docker/build-full-image.sh ] || abort NO-FILE "scripts/docker/build-full-image.sh is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
  candidate_tag
  SSHB=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
  "${SSHB[@]}" 'sudo -n docker info >/dev/null && echo ok' 2>/dev/null | grep -q ok || abort SSH "cannot ssh to $HOST, or ubuntu cannot run sudo docker" "" "ssh $HOST 'sudo -n docker info'   (send me this output)"
  SIZE_MB=$("${SSHB[@]}" "sudo -n du -sm $DATA 2>/dev/null | cut -f1" 2>/dev/null || true)
  case "$SIZE_MB" in ''|*[!0-9]*) abort NO-DATA "cannot read the size of $DATA on the server" "" "ssh $HOST 'sudo -n ls -ld $DATA'   (send me this output)";; esac
  [ "$SIZE_MB" -le "$MAX_MB" ] || abort DATA-BIG "the data folder on the server is $SIZE_MB MB, over the $MAX_MB MB limit for a copy" "" "PREVIEW_MAX_MB=$((SIZE_MB + 100)) ops preview   (only if you have the disk space)"

  echo "ops preview: candidate $IMAGE"
  echo "  What it does: builds/uses $IMAGE on the server, copies the image and the data folder ($SIZE_MB MB) to this Mac,"
  echo "  copies the live settings into a private temp file, runs the image on http://127.0.0.1:$PORT with execution OFF and no model key."
  echo "  The database is the REAL one: signing in and any button that writes (Grant Credits, Admin Stop, delete task, stop run) is a real change."
  echo "  File changes stay in the local copy. Ctrl-C removes the container and the temp folder."
  printf 'Type yes (English keyboard) to start, anything else changes nothing: '
  read -r A < "${PREVIEW_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "" "run ops preview again and type yes"

  trap cleanup EXIT INT TERM
  umask 077; WORK=$(mktemp -d "${TMPDIR:-/tmp}/requi-preview.XXXXXXXXXX"); chmod 700 "$WORK"; mkdir "$WORK/data"

  # 2. the image on the server, then here
  HAVE=1; "${SSHB[@]}" "sudo -n docker image inspect $IMAGE >/dev/null 2>&1" || HAVE=0
  if [ "$HAVE" = 1 ]; then
    echo "== the server already has $IMAGE: checking that it passes the smoke check before it is reused"
    SRC=0; REQUI_SERVER="$HOST" bash scripts/docker/smoke-image.sh "$IMAGE" || SRC=$?
    case "$SRC" in 0) ;; 1) HAVE=0; echo "== that image failed and its tag was removed (that exact tag only); building it again";; *) abort IMAGE-SMOKE "the existing image failed the smoke check and could not be removed" "" "send me the output above";; esac
  fi
  if [ "$HAVE" = 0 ]; then
    echo "== building $IMAGE on the server (build-full-image.sh asks its own yes; it publishes the tag only after the smoke check passes)"
    ( umask 022; bash scripts/docker/build-full-image.sh ) || abort BUILD "the server build did not finish" "Nothing was started on this Mac. The build script removes a candidate image that fails its smoke check." "send me this output"
  fi
  SRVID=$("${SSHB[@]}" "sudo -n docker image inspect --format '{{.Id}}' $IMAGE" 2>/dev/null || true)
  [ -n "$SRVID" ] || abort IMAGE-ID "cannot read the image id on the server" "" "send me this output"
  if [ "$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)" != "$SRVID" ]; then docker rmi "$IMAGE" >/dev/null 2>&1 || true; fi
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then echo "== image already on this Mac"; else
    echo "== copying the image from the server (a few GB the first time)"
    "${SSHB[@]}" "sudo -n docker save $IMAGE" | docker load >/dev/null || abort IMAGE-COPY "could not copy $IMAGE from the server" "" "docker image ls requi-full   (send me this output)"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || abort IMAGE-COPY "the image did not arrive" "" "docker image ls requi-full   (send me this output)"
  fi
  ARCH=$(docker image inspect --format '{{.Architecture}}' "$IMAGE" 2>/dev/null || true)
  [ "$ARCH" = amd64 ] || abort ARCH "the image is $ARCH, expected amd64 (the server build)" "" "send me this output"

  # 3. settings: live file to a private temp file, never printed
  "${SSHB[@]}" "sudo -n cat /etc/requi/full.env" > "$WORK/live.env" 2> "$WORK/ssh.err" || abort SETTINGS "cannot read /etc/requi/full.env on the server" "" "ssh $HOST 'sudo -n test -r /etc/requi/full.env && echo readable'"
  chmod 600 "$WORK/live.env"
  grep -q '^DELIVERY_DATABASE_URL=.' "$WORK/live.env" && grep -q '^DELIVERY_SUPABASE_URL=.' "$WORK/live.env" || abort SETTINGS "the live settings file has no database or Supabase address" "" "send me this output (the file itself must not be sent)"
  ART_DEF=no; ART_DIFF=no
  if [ -n "$CHK" ] && grep -q '^VMODEL_ARTIFACTS_DIR=' "$WORK/live.env"; then ART_DEF=yes; [ "$(grep '^VMODEL_ARTIFACTS_DIR=' "$WORK/live.env" | tail -1 | cut -d= -f2- | tr -d "\"'")" = /data/artifacts ] || ART_DIFF=yes; fi
  build_env "$WORK/live.env" "$WORK/preview.env" || abort NO-TOKEN "could not make the temporary engine token" "" "send me this output"; rm -f "$WORK/live.env"
  printf 'REQUI_EXPECTED_REPOS=%s\n' "$REPOS" >> "$WORK/preview.env"
  grep -q -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|API_KEY)' "$WORK/preview.env" && abort MODEL-KEY "a model key is still in the preview settings" "" "send me this output (not the file)"
  [ "$(grep -c '^DELIVERY_CLOUD_EXECUTION=' "$WORK/preview.env")" = 1 ] && grep -q '^DELIVERY_CLOUD_EXECUTION=0$' "$WORK/preview.env" || abort EXEC-ON "execution is not forced off in the preview settings" "" "send me this output"
  [ "$(grep -c '^DELIVERY_PUBLIC_ORIGIN=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's sign-in address is not set to its own local address" "" "send me this output"
  [ "$(grep -c '^DELIVERY_ALT_ORIGINS=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_ALT_ORIGINS=http://localhost:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's second sign-in address is not exactly localhost with its own port" "" "send me this output"

  # 4. can this Mac reach the database? (prints only OK or the error type, never the address)
  DBCHECK='import os,sys
try:
    import psycopg
    psycopg.connect(os.environ["DELIVERY_DATABASE_URL"],connect_timeout=10).execute("select 1")
    print("DBOK")
except Exception as e:
    print("DBFAIL",type(e).__name__)'
  OUT=$(docker run --rm --platform linux/amd64 --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$DBCHECK" 2>&1 | scrub | tail -1 || true)
  [ "$OUT" = DBOK ] || abort NO-DB "this Mac cannot open the database ($OUT)" "" "check your network or VPN; if it says OperationalError the database may only accept the server's address: send me this line"

  # 4b. the image's OWN startup rules, run on these exact settings. Prints problem names only, never a value.
  CONTRACT='import os,sys;sys.path.insert(0,"/app");import full_entry;p=full_entry.check_env(os.environ);print("CONTRACT-OK" if not p else "CONTRACT-PROBLEMS");[print("problem:",x) for x in p]'
  COUT=$(docker run --rm --platform linux/amd64 --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$CONTRACT" 2>&1 | scrub | tail -8 || true)
  echo "$COUT" | grep -qx CONTRACT-OK || abort NOT-STARTABLE "the image would not start with these settings: $(echo "$COUT" | grep '^problem:' | tr '\n' ' ')" "" "send me this output"

  # 5. the data folder, read only on the server, into the temp folder
  echo "== copying the server's data folder ($SIZE_MB MB), read only on the server"
  "${SSHB[@]}" "sudo -n tar -C $DATA --exclude='run_metrics.db*' -cf - ." | tar -x -C "$WORK/data" || abort DATA-COPY "could not copy $DATA from the server" "" "ssh $HOST 'sudo -n ls -ld $DATA'   (send me this output)"
  chmod -R a+rwX "$WORK/data"
  # 5b. optional, read only, yes/no only: is one session's engine state file on the server and in this copy? Nothing is read from the file, nothing is written.
  CHK_S=""; CHK_C=""
  if [ -n "$CHK" ]; then
    CHK_S=$("${SSHB[@]}" "sudo -n test -f $DATA/artifacts/$CHK/interview/http-session.json && echo YES || echo NO" 2>/dev/null | tail -1 || true)
    case "$CHK_S" in YES) CHK_S=yes;; NO) CHK_S=no;; *) CHK_S="could not check";; esac
    if [ -f "$WORK/data/artifacts/$CHK/interview/http-session.json" ]; then CHK_C=yes; else CHK_C=no; fi
  fi

  # 6. run it
  docker run -d --name "$NAME" --platform linux/amd64 --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g --memory-swap 2g \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$WORK/data":/data --env-file "$WORK/preview.env" -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || abort RUN "the container did not start" "" "docker logs $NAME   (send me this output)"
  STARTED=1
  OK=0
  for i in $(seq 1 "${PREVIEW_WAIT_TRIES:-90}"); do
    if curl -fs --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then OK=1; break; fi
    docker ps --format '{{.Names}}' | grep -qx "$NAME" || break
    sleep "${PREVIEW_POLL:-2}"
  done
  if [ "$OK" != 1 ]; then echo "== the container did not become healthy; last log lines:"; docker logs --tail 30 "$NAME" 2>&1 | scrub; abort NOT-HEALTHY "the preview did not start" "" "send me the log lines above"; fi
  # readiness needs POSITIVE evidence, and anything missing or unknown fails closed:
  # (1) the app's own startup line says CLOSED and never OPEN, (2) the running container's own environment says execution is 0 and holds no provider key.
  LOGS=$(docker logs "$NAME" 2>&1 | scrub)
  echo "$LOGS" | grep -q 'Cloud execution: CLOSED' || abort EXEC-UNKNOWN "the app did not report 'Cloud execution: CLOSED' at startup" "" "send me this output"
  ! echo "$LOGS" | grep -q 'Cloud execution: OPEN' || abort EXEC-OPEN "the app reports execution OPEN" "" "send me this output"
  PROBE='import os,re;print("EXEC="+os.environ.get("DELIVERY_CLOUD_EXECUTION","unset"));print("KEYS="+str(len([k for k in os.environ if re.search("DEEPSEEK|OPENAI|ANTHROPIC|API_KEY",k,re.I)])))'
  EXECOUT=$(docker exec "$NAME" python -c "$PROBE" 2>/dev/null || true)
  echo "$EXECOUT" | grep -qx 'EXEC=0' && echo "$EXECOUT" | grep -qx 'KEYS=0' || abort EXEC-UNVERIFIED "the running container's environment does not confirm execution off and no provider key" "" "send me this output"
  echo
  echo "PREVIEW READY: http://127.0.0.1:$PORT   (execution CLOSED, no model key, real database, real sign-in)"
  echo "Open it as http://127.0.0.1:$PORT or http://localhost:$PORT: sign-in accepts only these two addresses. Each address keeps its own sign-in session."
  echo "Running build: $(printf '%s' "$REPOS" | sed 's/=/ /g; s/,/, /g')"
  if [ -n "$CHK" ]; then
    echo "Session check (read only, yes or no only; no file contents, no counts):"
    echo "  engine state file for the session you named, on the server: $CHK_S"
    echo "  engine state file for it in this preview's copy: $CHK_C"
    echo "  live settings define VMODEL_ARTIFACTS_DIR: $ART_DEF; differs from the image default /data/artifacts: $ART_DIFF"
    echo "  This shows only whether that file exists at these two paths. It says nothing about any other place. A live setting that differs is not passed to the preview, so the server path may not be where the engine keeps it."
  fi
  echo "Warning: Grant Credits, Admin Stop, delete task and stop run change REAL data. File changes stay in the local copy."
  echo "Press Ctrl-C here when you are done."
  docker logs -f "$NAME" 2>&1 | scrub || true
}

if [ "${PREVIEW_FUNCTIONS_ONLY:-0}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

. "$(dirname "$0")/lib.sh"; ops_start preview "$0"; REPO=$PWD
if [ "${PREVIEW_VIA_SERVER:-0}" = 1 ]; then server_flow; else local_flow; fi
