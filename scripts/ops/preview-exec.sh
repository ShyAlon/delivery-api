#!/usr/bin/env bash
# Requi.re local EXECUTION preview: the candidate image on this Mac, against the LIVE database, with execution ON.
# Same admitted in-process host and the same per-call admission and billing ledger as the cloud. A real interview
# run here spends real money for a person who has an admission row and prepaid credits; anyone else is answered
# 'not admitted'. Starting this preview is NOT the activation approval for the first real interview: the spend
# limit, cost ceiling, model route and live changes are approved once, separately, before that interview.
# Settings: the file saved on this Mac by scripts/ops/preview-provision.sh (~/.config/requi/preview.env), plus
# vendor prices and limits pinned fresh from the two public DeepSeek pages by scripts/ops/pin.py (the exact step
# ops go-live runs; public GETs only, never a paid call). The DeepSeek key is read from this shell's environment
# (DEEPSEEK_API_KEY) when present and is passed straight to the container - it is never written to any file,
# never printed, never sent anywhere new. If any prerequisite is missing this stops with a clear message; it
# never falls back to another model route. Ctrl-C removes the container and the temp folder.
set -euo pipefail
PORT=${PREVIEW_EXEC_PORT:-8001}
NAME=requi-preview-exec; WORK=""; STARTED=0
. "$(dirname "$0")/lib.sh"; ops_start preview-exec "$0" --venv
REPO=$PWD

scrub(){ sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g; s#sk-[A-Za-z0-9_-]+#sk-[hidden]#g'; }
cleanup(){
  rc=$?; trap - EXIT INT TERM
  if [ "$STARTED" = 1 ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
  [ -z "$WORK" ] || rm -rf "$WORK"
  [ "$STARTED" != 1 ] || echo "== preview-exec stopped: container $NAME removed, temp folder deleted (the image stays, it holds no settings)"
  exit $rc
}

candidate_tag(){
  ROOT=${REQUI_REPOS_ROOT:-$(dirname "$REPO")}; TAG=""; REPOS=""
  for r in delivery-api interview-engine spec-generator verification-loop; do
    D=$ROOT/$r; git -C "$D" rev-parse --is-inside-work-tree >/dev/null 2>&1 || abort "NO-$r" "no checkout at $D" "" "git clone https://github.com/ShyAlon/$r $D"
    git -C "$D" fetch -q origin main || abort "FETCH-$r" "cannot fetch $r" "" "git -C $D fetch origin main"
    [ -z "$(git -C "$D" status --porcelain --untracked-files=no)" ] || abort "DIRTY-$r" "$r has uncommitted changes to tracked files" "" "git -C $D status --short   (commit, stash or discard them)"
    [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" = main ] || abort "BRANCH-$r" "$r is not on main" "" "git -C $D checkout main"
    git -C "$D" pull -q --ff-only origin main || abort "PULL-$r" "$r main cannot fast-forward to origin/main" "" "git -C $D log --oneline origin/main..main"
    [ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$D" rev-parse origin/main)" ] || abort "STALE-$r" "$r is not at origin/main after pulling" "" "git -C $D status"
    S=$(git -C "$D" rev-parse --short=8 HEAD); TAG="${TAG:+$TAG-}$S"; REPOS="${REPOS:+$REPOS,}$r=$S"
  done
  IMAGE=requi-full:$TAG
}

build_exec_env(){
  ( umask 077; : > "$2"; chmod 600 "$2"
    grep -E '^DELIVERY_[A-Z0-9_]*=' "$1" | grep -v -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|MISTRAL|GROQ|COHERE|XAI|PROVIDER|API_KEY|MODEL_KEY)[A-Z0-9_]*=' \
      | grep -v -E '^(DELIVERY_CLOUD_EXECUTION|DELIVERY_COOKIE_SECURE|DELIVERY_PREVIEW|DELIVERY_LOCAL_PREVIEW|DELIVERY_METRICS_DB|DELIVERY_HOST|DELIVERY_PORT|DELIVERY_CONTROL_PLANE_ONLY|DELIVERY_PUBLIC_ORIGIN|DELIVERY_ALT_ORIGINS)=' >> "$2" || true
    # The engine token is a password between the two processes inside the preview container only. It is made fresh here (never the live one, which the allowlist drops).
    tok=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); [ ${#tok} = 48 ] || exit 1
    printf '%s\n' "INTERVIEW_ENGINE_TOKEN=$tok" DELIVERY_CONTROL_PLANE_ONLY=0 DELIVERY_CLOUD_EXECUTION=1 DELIVERY_COOKIE_SECURE=0 DELIVERY_PREVIEW=1 DELIVERY_METRICS_DB=/tmp/run_metrics.db DELIVERY_HOST=0.0.0.0 DELIVERY_PORT=8000 "DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT" "DELIVERY_ALT_ORIGINS=http://localhost:$PORT" >> "$2" ) || return 1
  chmod 600 "$2"
}

for t in docker curl git shasum tar; do command -v $t >/dev/null 2>&1 || abort "TOOL-$t" "$t is missing" "" "install $t (Docker: open Docker Desktop once, or brew install --cask docker)"; done
docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"
case "$PORT" in ''|*[!0-9]*) abort PORT "PREVIEW_EXEC_PORT is not a number" "" "unset PREVIEW_EXEC_PORT";; esac
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then abort PORT-BUSY "something already listens on 127.0.0.1:$PORT" "" "stop it, or: PREVIEW_EXEC_PORT=8011 ops preview-exec"; fi
docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && abort LEFTOVER "a container named $NAME is left over from an earlier run" "" "docker rm -f $NAME"
# Two provider modes. deepseek (default): the paid managed route, exactly as before. ollama: the operator's
# local model at $0 - no key, no vendor pages, the reviewed row and ceiling grounded from this Mac's server.
PROVIDER=${PREVIEW_EXEC_PROVIDER:-deepseek}
case "$PROVIDER" in deepseek|ollama) ;; *) abort PROVIDER "PREVIEW_EXEC_PROVIDER must be deepseek or ollama, not '$PROVIDER'" "" "unset PREVIEW_EXEC_PROVIDER";; esac
OLLAMA_MODEL=${PREVIEW_EXEC_OLLAMA_MODEL:-qwen2.5-coder:14b}
OLLAMA_HOST=${OLLAMA_HOST:-http://localhost:11434}

PINFILES="scripts/ops/pin.py src/delivery_api/vendor_sources.py src/delivery_api/retail_pricing.py"
[ "$PROVIDER" = deepseek ] || PINFILES="scripts/ops/pin-local.py src/delivery_api/ollama_usage.py src/delivery_api/local_route.py src/delivery_api/provider_network.py"
for f in $PINFILES src/delivery_api/production_gateway.py; do [ -f "$f" ] || abort NO-FILE "$f is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"; done

# The provider key comes from this shell's own protected environment, never from a new file or a prompt.
if [ "$PROVIDER" = deepseek ]; then
[ -n "${DEEPSEEK_API_KEY:-}" ] || abort NO-KEY "DEEPSEEK_API_KEY is not in this shell's environment" "Nothing was started and the database was not touched." "export DEEPSEEK_API_KEY=... in the shell that runs this (your existing protected local environment), then run ops preview-exec again"
case "$DEEPSEEK_API_KEY" in *[[:space:]]*) abort KEY-SPACE "DEEPSEEK_API_KEY has a space or line break in it" "" "export only the key, then run again";; esac
[ "${#DEEPSEEK_API_KEY}" -ge 20 ] || abort KEY-SHORT "DEEPSEEK_API_KEY is too short to be a DeepSeek key" "" "export the whole key, then run again"
fi

# 1. the candidate: the four checkouts at origin/main, exactly as deploy-full takes them
[ -f scripts/docker/build-local-image.sh ] || abort NO-FILE "scripts/docker/build-local-image.sh is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
candidate_tag
MACHINE=$(uname -m); case "$MACHINE" in arm64|aarch64) NATIVE=arm64;; x86_64|amd64) NATIVE=amd64;; *) abort ARCH "unknown machine architecture $MACHINE";; esac
EXPECTED_ARCH=${PREVIEW_ARCH:-$NATIVE}; case "$EXPECTED_ARCH" in arm64|amd64) ;; *) abort ARCH "PREVIEW_ARCH must be arm64 or amd64, not '$EXPECTED_ARCH'" "" "unset PREVIEW_ARCH";; esac
PLATFORM=linux/$EXPECTED_ARCH

# 2. the settings saved on this Mac by ops preview-provision (one time), never printed
STORE=${PREVIEW_SETTINGS_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/requi/preview.env}
[ -f "$STORE" ] || abort NOT-PROVISIONED "no preview settings are saved on this Mac yet" "" "bash scripts/ops/preview-provision.sh   (one time: saves the live settings on this Mac, mode 600)"
[ -r "$STORE" ] || abort NOT-PROVISIONED "the saved settings file is not readable: $STORE" "" "ls -l $STORE"
PERM=$(stat -c %a "$STORE" 2>/dev/null || stat -f %Lp "$STORE")
[ "$PERM" = 600 ] || abort SETTINGS-PERMS "the saved settings file is mode $PERM, not 600; it holds real credentials" "" "chmod 600 $STORE"
grep -q '^DELIVERY_DATABASE_URL=.' "$STORE" && grep -q '^DELIVERY_SUPABASE_URL=.' "$STORE" || abort SETTINGS "the saved settings file has no database or Supabase address" "" "bash scripts/ops/preview-provision.sh   (save them again)"

echo "ops preview-exec: candidate $IMAGE (built on this Mac, no server access)"
echo "  What it does: runs $IMAGE on http://127.0.0.1:$PORT with execution ON against the LIVE database."
if [ "$PROVIDER" = deepseek ]; then
echo "  Provider: DeepSeek (paid). Vendor prices and limits are pinned fresh from the two public DeepSeek pages"
echo "  (public GETs, nothing paid), with the configured safety ceiling 128000 tokens per call / 16384 max output."
echo "  The DeepSeek key comes from this shell's environment and is passed to the container without touching disk."
echo "  Real spend is possible only for a person with an admission row and prepaid credits; anyone else is 'not admitted'."
else
echo "  Provider: local Ollama model $OLLAMA_MODEL at $OLLAMA_HOST (\$0, nothing paid, no key needed)."
echo "  The reviewed row and context ceiling are grounded from this Mac's own Ollama server and the candidate"
echo "  checkouts; every call is still reserved and settled in the shared ledger at \$0. The container reaches"
echo "  the server through host.docker.internal."
fi
echo "  This start is NOT the activation approval for the first real interview: spend limit, cost ceiling, model"
echo "  route and live changes are approved once, separately, before that interview."
printf 'Type yes (English keyboard) to start, anything else changes nothing: '
read -r A < "${PREVIEW_TTY:-/dev/tty}" || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "" "run ops preview-exec again and type yes"

trap cleanup EXIT INT TERM
umask 077; WORK=$(mktemp -d "${TMPDIR:-/tmp}/requi-preview-exec.XXXXXXXXXX"); chmod 700 "$WORK"; mkdir "$WORK/data"

# 3. settings: saved file to a private temp file, never printed, execution ON, local-preview flag on
build_exec_env "$STORE" "$WORK/preview.env" || abort NO-TOKEN "could not make the temporary engine token" "" "send me this output"
printf 'DELIVERY_LOCAL_PREVIEW=1\n' >> "$WORK/preview.env"
if [ "$PROVIDER" = ollama ]; then
  OPORT=$(printf '%s' "$OLLAMA_HOST" | sed -E 's#^[a-z]+://([^:/]+):?([0-9]*).*#\2#'); OPORT=${OPORT:-11434}
  printf 'OLLAMA_BASE_URL=%s\n' "http://host.docker.internal:$OPORT/v1" >> "$WORK/preview.env"
  [ -z "${VMODEL_OLLAMA_NUM_CTX:-}" ] || printf 'VMODEL_OLLAMA_NUM_CTX=%s\n' "$VMODEL_OLLAMA_NUM_CTX" >> "$WORK/preview.env"
fi
chmod 600 "$WORK/preview.env"
grep -q -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|API_KEY)' "$WORK/preview.env" && abort MODEL-KEY "a model key is in the saved settings file; the key must come from this shell's environment only" "" "remove it from $STORE (it does not belong there)"
[ "$(grep -c '^DELIVERY_CLOUD_EXECUTION=1$' "$WORK/preview.env")" = 1 ] || abort EXEC-OFF "execution is not forced on in the preview-exec settings" "" "send me this output"
[ "$(grep -c '^DELIVERY_PUBLIC_ORIGIN=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's sign-in address is not set to its own local address" "" "send me this output"
[ "$(grep -c '^DELIVERY_ALT_ORIGINS=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_ALT_ORIGINS=http://localhost:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's second sign-in address is not exactly localhost with its own port" "" "send me this output"
[ "$(grep -c '^DELIVERY_PREVIEW=1$' "$WORK/preview.env")" = 1 ] || abort PREVIEW-FLAG "the preview flag is not set exactly once" "" "send me this output"

# 4. reviewed prices and limits. deepseek: the exact pin step ops go-live runs (public GETs, nothing paid).
# ollama: grounded from this Mac's own server and the candidate checkouts (local HTTP, nothing paid).
if [ "$PROVIDER" = ollama ]; then
echo "== grounding the local Ollama model and context window from this Mac's server (local HTTP, nothing paid)"
rc=0; PYTHONPATH=src REQUI_REPOS_ROOT="$ROOT" PREVIEW_EXEC_OLLAMA_MODEL="$OLLAMA_MODEL" OLLAMA_HOST="$OLLAMA_HOST" .venv/bin/python "$(dirname "$0")/pin-local.py" "$WORK" "$ROOT" > "$WORK/pin-out.txt" 2>&1 || rc=$?
else
echo "== reading the two DeepSeek pages (public GET, nothing paid)"
rc=0; PYTHONPATH=src .venv/bin/python "$(dirname "$0")/pin.py" "$WORK" > "$WORK/pin-out.txt" 2>&1 || rc=$?
fi
if [ "$rc" != 0 ]; then
  tail -15 "$WORK/pin-out.txt"
  case "$(tail -1 "$WORK/pin-out.txt")" in PIN-FAIL*) abort PIN "see the line above" "" "send me this output; the page or the parser changed, and I have to look before anything is pinned";; *) abort PIN-CRASH "the pin step crashed" "" "send me this whole output";; esac
fi
cat "$WORK/pin-out.txt"
[ -s "$WORK/cloud-models.env" ] || abort PIN-EMPTY "the pin step wrote no settings" "" "send me this output"
grep -q '^VMODEL_REVIEWED_MODEL_PRICES=.' "$WORK/cloud-models.env" && grep -q '^VMODEL_REVIEWED_CONTEXT_CEILING=.' "$WORK/cloud-models.env" || abort PIN-PARTIAL "the pinned settings are incomplete" "" "send me this output (not the files)"

# 5. the image on this Mac, built here and smoke checked
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

# 6. can this Mac reach the database? (prints only OK or the error type, never the address)
DBCHECK='import os,sys
try:
    import psycopg
    psycopg.connect(os.environ["DELIVERY_DATABASE_URL"],connect_timeout=10).execute("select 1")
    print("DBOK")
except Exception as e:
    print("DBFAIL",type(e).__name__)'
OUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$DBCHECK" 2>&1 | scrub | tail -1 || true)
[ "$OUT" = DBOK ] || abort NO-DB "this Mac cannot open the database ($OUT)" "" "check your network or VPN; if it says OperationalError the database may not accept this Mac's address: send me this line"

# 7. the image's OWN startup rules, run on these exact settings plus the pinned prices and the key. Prints problem names only, never a value.
CONTRACT='import os,sys;sys.path.insert(0,"/app");import full_entry;p=full_entry.check_env(os.environ);print("CONTRACT-OK" if not p else "CONTRACT-PROBLEMS");[print("problem:",x) for x in p]'
KEYPASS=(); [ "$PROVIDER" = deepseek ] && KEYPASS=(-e DEEPSEEK_API_KEY)
COUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" --env-file "$WORK/cloud-models.env" ${KEYPASS[@]+"${KEYPASS[@]}"} "$IMAGE" python -c "$CONTRACT" 2>&1 | scrub | tail -8 || true)
echo "$COUT" | grep -qx CONTRACT-OK || abort NOT-STARTABLE "the image would not start with these settings: $(echo "$COUT" | grep '^problem:' | tr '\n' ' ')" "" "send me this output"

# 8. run it (the data folder is an empty local one: no server copy)
docker run -d --name "$NAME" --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g --memory-swap 2g \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$WORK/data":/data --env-file "$WORK/preview.env" --env-file "$WORK/cloud-models.env" \
  ${KEYPASS[@]+"${KEYPASS[@]}"} --add-host host.docker.internal:host-gateway -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || abort RUN "the container did not start" "" "docker logs $NAME   (send me this output)"
STARTED=1
OK=0
for i in $(seq 1 "${PREVIEW_WAIT_TRIES:-90}"); do
  if curl -fs --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then OK=1; break; fi
  docker ps --format '{{.Names}}' | grep -qx "$NAME" || break
  sleep "${PREVIEW_POLL:-2}"
done
if [ "$OK" != 1 ]; then echo "== the container did not become healthy; last log lines:"; docker logs --tail 30 "$NAME" 2>&1 | scrub; abort NOT-HEALTHY "the preview did not start" "" "send me the log lines above"; fi
# readiness needs POSITIVE evidence: the app's own startup line says OPEN, and the running container's own
# environment confirms execution 1 with exactly one provider key variable (its value is never printed).
LOGS=$(docker logs "$NAME" 2>&1 | scrub)
echo "$LOGS" | grep -q 'Cloud execution: OPEN' || abort EXEC-UNKNOWN "the app did not report 'Cloud execution: OPEN' at startup" "" "send me this output"
PROBE='import os,re;print("EXEC="+os.environ.get("DELIVERY_CLOUD_EXECUTION","unset"));print("KEYS="+str(len([k for k in os.environ if re.search("DEEPSEEK|OPENAI|ANTHROPIC|API_KEY",k,re.I)])))'
EXECOUT=$(docker exec "$NAME" python -c "$PROBE" 2>/dev/null || true)
EXPECTED_KEYS=1; [ "$PROVIDER" = ollama ] && EXPECTED_KEYS=0
echo "$EXECOUT" | grep -qx 'EXEC=1' && echo "$EXECOUT" | grep -qx "KEYS=$EXPECTED_KEYS" || abort EXEC-UNVERIFIED "the running container's environment does not confirm execution on with the expected provider key count ($EXPECTED_KEYS)" "" "send me this output"
echo
echo "PREVIEW-EXEC READY: http://127.0.0.1:$PORT   (execution OPEN, live database, real sign-in, provider: $PROVIDER)"
[ "$PROVIDER" = ollama ] && echo "Local Ollama route: $OLLAMA_MODEL at \$0; every call is ledgered. The managed DeepSeek route is not configured in this mode."
echo "Spend is possible only for a person with an admission row and prepaid credits."
echo "Running build: $(printf '%s' "$REPOS" | sed 's/=/ /g; s/,/, /g')"
echo "Reminder: the first real interview still needs its own one-time approval (spend limit, cost ceiling, model route, live changes)."
echo "Press Ctrl-C here when you are done."
docker logs -f "$NAME" 2>&1 | scrub || true
