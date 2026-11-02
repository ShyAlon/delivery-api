#!/usr/bin/env bash
# Requi.re local EXECUTION preview: the candidate image on this Mac, against the LIVE database, with execution ON.
# Same admitted in-process host and the same per-call admission and billing ledger as the cloud. A real interview
# run here spends real money for a person who has an admission row and prepaid credits; anyone else is answered
# 'not admitted'. Starting this preview is NOT the activation approval for the first real interview: the spend
# limit, cost ceiling, model route and live changes are approved once, separately, before that interview.
# Settings: private preview/env credentials and one durable public model pin store.
# Back office > Model pins owns all price refresh/review/edit/default changes.
# No launcher price generator or filesystem pin cache. Paid route visibility is not spend approval.
# Ctrl-C removes only this invocation's container; data volume is retained.
set -euo pipefail

PORT=${PREVIEW_EXEC_PORT:-8001}
NAME=requi-preview-exec; WORK=""; STARTED=0; KEEP_FOR_INSPECTION=0
BACKOFFICE_PID=""; BACKOFFICE_PREVIEW_PORT=${PREVIEW_BACKOFFICE_PORT:-8020}
DATA_VOLUME=${PREVIEW_DATA_VOLUME:-requi-preview-data}
. "$(dirname "$0")/lib.sh"
# Settings may re-exec the launcher. Complete that handoff before any run header.
REQUIRED=DELIVERY_DATABASE_URL,DELIVERY_SUPABASE_URL,DELIVERY_SUPABASE_ANON_KEY
[ "${PREVIEW_EXEC_PROVIDER:-both}" = ollama ] || REQUIRED="$REQUIRED,DEEPSEEK_API_KEY"
ops_start preview-exec "$0" --venv "$REQUIRED"
REPO=$PWD

scrub(){ sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g; s#sk-[A-Za-z0-9_-]+#sk-[hidden]#g'; }
# Own only the child created here. Never reuse or terminate an unrelated listener.
stop_backoffice(){
  if [ -n "${BACKOFFICE_PID:-}" ]; then
    kill "$BACKOFFICE_PID" >/dev/null 2>&1 || true
    wait "$BACKOFFICE_PID" 2>/dev/null || true
    BACKOFFICE_PID=""
  fi
}
start_backoffice(){
  .venv/bin/python - "$BACKOFFICE_PREVIEW_PORT" "$PORT" <<'PYPORT' || return 1
import socket,sys
try:
    port=int(sys.argv[1]);upstream=int(sys.argv[2])
    if not 1<=port<=65535 or port in (upstream,8000,8010):raise ValueError('Back office port conflicts with a service port')
    with socket.socket() as s:s.bind(('127.0.0.1',port))
except (ValueError,OSError) as error:
    print('Back office cannot own its private port: '+str(error),file=sys.stderr);raise SystemExit(1)
PYPORT
  BACKOFFICE_HOST=127.0.0.1 BACKOFFICE_PORT="$BACKOFFICE_PREVIEW_PORT" BACKOFFICE_SERVICE_URL="http://127.0.0.1:$PORT" \
    .venv/bin/python -m delivery_api.backoffice > "$WORK/backoffice.log" 2>&1 &
  BACKOFFICE_PID=$!
  for _ in $(seq 1 40); do
    kill -0 "$BACKOFFICE_PID" 2>/dev/null || break
    if curl -fs --max-time 1 "http://127.0.0.1:$BACKOFFICE_PREVIEW_PORT/health" 2>/dev/null | .venv/bin/python -c 'import json,sys; x=json.load(sys.stdin);sys.exit(0 if x.get("ok") is True and x.get("role")=="backoffice" else 1)' 2>/dev/null; then
      # A racing/foreign listener cannot count as our child's successful bind.
      sleep .1
      kill -0 "$BACKOFFICE_PID" 2>/dev/null && return 0
      break
    fi
    sleep .1
  done
  echo "Back office did not become healthy; child log:"
  scrub < "$WORK/backoffice.log"
  stop_backoffice
  return 1
}
cleanup(){
  rc=$?; trap - EXIT INT TERM
  if [ "$rc" != 0 ]; then .venv/bin/python scripts/ops/preview-debug.py --code "${FAIL_CODE:-UNEXPECTED-$rc}" --step "${STEP_NAME:-startup}" || warn "WARNING debug ZIP unavailable; run req debug from this checkout."; fi
  stop_backoffice
  if [ "$STARTED" = 1 ] && [ "${KEEP_FOR_INSPECTION:-0}" != 1 ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
  [ -z "$WORK" ] || rm -rf "$WORK"
  if [ "${KEEP_FOR_INSPECTION:-0}" = 1 ]; then
    echo "== preview retained for inspection: $NAME (execution remains ON against the live DB; no interview started by smoke)"
    echo "Inspect: docker logs --tail 100 $NAME"
    echo "Stop/remove when done: docker rm -f $NAME"
  fi
  [ "$STARTED" != 1 ] || [ "${KEEP_FOR_INSPECTION:-0}" = 1 ] || echo "== preview-exec stopped: container $NAME removed, temporary settings deleted; persistent data volume retained (the image holds no settings)"
  exit $rc
}

STEPS_TOTAL=9; STEP_N=0; STEP_NAME=''; STEP_START=0
step(){ STEP_N=$((STEP_N+1)); STEP_NAME=$1; STEP_START=$SECONDS; echo "== [$STEP_N/$STEPS_TOTAL] $STEP_NAME"; }
step_ok(){ echo "   ok ($((SECONDS-STEP_START))s)"; STEP_NAME=''; }
# Local abort: lib.sh's format, plus the step and elapsed time when a step is running.
abort(){
  FAIL_CODE=$1
  echo; error "ABORTED [$1]: $2"
  echo "Debug: req debug (ZIP under $REPO/runs/preview-debug)"
  if [ "$1" = BROWSER-SMOKE ]; then
    echo "browser errors repeated for inspection (anonymous smoke, no sign-in):"
    [ -z "${BROWSER_OUT:-}" ] || printf '%s\n' "$BROWSER_OUT"
  fi
  [ -z "$STEP_NAME" ] || error "Step $STEP_N/$STEPS_TOTAL '$STEP_NAME' failed after $((SECONDS-STEP_START))s."
  echo "${3:-Nothing was started and the database was not touched.}"
  echo "Fix: ${4:-run scripts/ops/$OPS_NAME.sh again; if it repeats, send me this whole output}"
  echo "Then run: scripts/ops/$OPS_NAME.sh"; exit 1
}

_PROMPT9=0
prompt_open(){
  # All prompts share one terminal handle, so a scripted PREVIEW_TTY=file advances line by line
  # across the provider pick and the typed yes.
  [ "$_PROMPT9" = 1 ] && return 0
  exec 9< "${PREVIEW_TTY:-/dev/tty}" 2>/dev/null && _PROMPT9=1
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
    grep -E '^(DELIVERY_[A-Z0-9_]*|REQUI_OPERATOR_SUBJECT|REQUI_OPERATOR_REGISTRY_VERSION|REQUI_OPERATOR_CAPABILITIES)=' "$1" | grep -v -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|MISTRAL|GROQ|COHERE|XAI|PROVIDER|API_KEY|MODEL_KEY)[A-Z0-9_]*=' \
      | grep -v -E '^(DELIVERY_CHILD_EXECUTION|DELIVERY_PAID_CHILD_PREVIEW|DELIVERY_CLOUD_EXECUTION|DELIVERY_COOKIE_SECURE|DELIVERY_PREVIEW|DELIVERY_LOCAL_PREVIEW|DELIVERY_METRICS_DB|DELIVERY_HOST|DELIVERY_PORT|DELIVERY_CONTROL_PLANE_ONLY|DELIVERY_PUBLIC_ORIGIN|DELIVERY_ALT_ORIGINS)=' >> "$2" || true
    # The engine token is a password between the two processes inside the preview container only. It is made fresh here (never the live one, which the allowlist drops).
    tok=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); [ ${#tok} = 48 ] || exit 1
    # REQUI_EXPECTED_REPOS pins the build this launcher asked for, so the image's own build-staleness
    # check can compare it against the REQUI_BUILD_REPOS the image was built with (preview.sh pins it
    # too, but this script builds its env itself - that pin never covered this path).
    printf '%s\n' "INTERVIEW_ENGINE_TOKEN=$tok" DELIVERY_CONTROL_PLANE_ONLY=0 DELIVERY_CLOUD_EXECUTION=1 DELIVERY_COOKIE_SECURE=0 DELIVERY_PREVIEW=1 DELIVERY_METRICS_DB=/data/requi-re/run_metrics.db DELIVERY_HOST=0.0.0.0 DELIVERY_PORT=8000 "DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT" "DELIVERY_ALT_ORIGINS=http://localhost:$PORT" "REQUI_EXPECTED_REPOS=$REPOS" >> "$2" ) || return 1
  chmod 600 "$2"
}

cat <<'PLAN'
Plan (9 steps, fail fast - cheapest and most likely first; every step prints its elapsed time, every failure names its step):
  1 inputs and environment (~5s): tools, Docker, port, saved settings, the four checkouts at origin/main, LIVE DB schema + app role readiness
  2 source smoke (~15s): the image's startup checks against the candidate sources - syntax parse, the preflight's own tests, the engine surface
  3 confirmation (you): provider pick if unset, DeepSeek key presence (deepseek only), what will run, one typed yes
  4 settings + shared model pin store (Back office owns refresh and review)
  5 build (0s if the image is current; otherwise ~10-30 min - no second yes, step 3 covered it)
  6 image smoke (~30s): the image's own full smoke - no network, no provider key, not root
  7 database + startability (~20s): this Mac opens the database; the image would start with these settings
  8 start + readiness (~1 min): container up, healthy, execution and provider key verified from inside
  9 browser smoke (~10s): the real page loads in a browser - any browser error beyond the page's own expected anonymous 401 probes (each named in the output) or a boot stuck on "Loading workspace…" fails the preview (anonymous, read-only: no sign-in against the live database)
PLAN

step "inputs and environment"
for t in docker curl git shasum tar; do command -v $t >/dev/null 2>&1 || abort "TOOL-$t" "$t is missing" "" "install $t (Docker: open Docker Desktop once, or brew install --cask docker)"; done
docker info >/dev/null 2>&1 || abort NO-DOCKER "Docker is installed but not running" "" "open -a Docker   (wait until it says running)"
case "$PORT" in ''|*[!0-9]*) abort PORT "PREVIEW_EXEC_PORT is not a number" "" "unset PREVIEW_EXEC_PORT";; esac
# Replace only our positively identified preview. Graceful stop, container only, no volume removal.
if docker container inspect "$NAME" >/dev/null 2>&1; then
  identity=$(docker container inspect "$NAME" | .venv/bin/python scripts/ops/preview-container-identity.py "$PORT" "$DATA_VOLUME") || abort PREVIEW-IDENTITY "named container is not a verified owned preview" "Nothing stopped; persistent data unchanged." "Open Debug or send this failure code; unrelated containers are never removed."
  own_id=${identity#OWNED }
  echo "Replacing our previous preview container $own_id; waiting up to 30s for graceful shutdown, data volume retained."
  docker kill --signal TERM "$own_id" >/dev/null 2>&1 || true
  stopped=0
  for _ in $(seq 1 30); do
    [ "$(docker inspect --format '{{.State.Running}}' "$own_id" 2>/dev/null)" = false ] && { stopped=1;break; }
    sleep 1
  done
  [ "$stopped" = 1 ] || abort PREVIEW-STOP "owned preview did not stop gracefully; no force kill was sent" "Container not removed; inspect shutdown evidence."
  docker rm "$own_id" >/dev/null || abort PREVIEW-REMOVE "stopped owned preview could not be removed" "Data volume retained."
fi
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  port_busy_details "$PORT" "$NAME"
  abort PORT-BUSY "a listener already occupies 127.0.0.1:$PORT; listener evidence is printed above" "" "use the reviewed stop instruction above, or: PREVIEW_EXEC_PORT=8011 req preview"
fi

MACHINE=$(uname -m); case "$MACHINE" in arm64|aarch64) NATIVE=arm64;; x86_64|amd64) NATIVE=amd64;; *) abort ARCH "unknown machine architecture $MACHINE";; esac
EXPECTED_ARCH=${PREVIEW_ARCH:-$NATIVE}; case "$EXPECTED_ARCH" in arm64|amd64) ;; *) abort ARCH "PREVIEW_ARCH must be arm64 or amd64, not '$EXPECTED_ARCH'" "" "unset PREVIEW_ARCH";; esac
PLATFORM=linux/$EXPECTED_ARCH
# the candidate: the four checkouts at origin/main, exactly as deploy-full takes them
[ -f scripts/docker/build-local-image.sh ] || abort NO-FILE "scripts/docker/build-local-image.sh is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
candidate_tag
PINFILES="src/delivery_api/model_pins.py scripts/ops/pin.py src/delivery_api/vendor_sources.py src/delivery_api/retail_pricing.py scripts/ops/pin-local.py src/delivery_api/ollama_usage.py src/delivery_api/local_route.py src/delivery_api/provider_network.py"
for f in $PINFILES src/delivery_api/production_gateway.py; do [ -f "$f" ] || abort NO-FILE "$f is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"; done
# The venv must carry what the steps ahead import - the pool redesign added psycopg-pool and older
# venvs predate it (ops_venv installs -e . without extras, so even a fresh ops venv lacks it). Fail
# here with the exact install line, not inside a step-2 pytest import traceback.
rc=0; .venv/bin/python scripts/ops/venv_check.py || rc=$?
[ "$rc" = 0 ] || abort VENV-DEPS "the checkout's .venv is missing declared dependencies" "Nothing was started." "run the exact pip line printed above, then run ops preview-exec again"
# Cheap read-only schema proof before local model grounding or image build.
.venv/bin/python scripts/ops/schema-readiness.py --offer-apply || abort SCHEMA-NOT-READY "schema review, apply, or restricted-role readiness did not complete" "Nothing built or started. If apply began, earlier APPLIED files persist; inspect the review output." "resolve the named review blocker and rerun req preview; do not send credentials"
step_ok

# Fast mode is allowed only for the exact immutable candidate identity.
FAST_CURRENT=0
case "${PREVIEW_CHECKS:-full}" in full) ;; fast)
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    IDENTITIES=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" 2>/dev/null | grep '^REQUI_BUILD_REPOS=' || true)
    if [ "$IDENTITIES" = "REQUI_BUILD_REPOS=$REPOS" ]; then FAST_CURRENT=1
    else echo 'FAST request: exact image identity not proven; running full checks instead.'; fi
  else echo 'FAST request: exact image absent; running full checks/build instead.'; fi;;
 *) abort CHECKS 'PREVIEW_CHECKS must be fast or full' '' 'use req preview --apply --fast or --full-checks';;
esac
if [ "$FAST_CURRENT" = 1 ]; then
  step "source smoke skipped: --fast and exact image identity verified"
  step_ok
else
step "source smoke: the image's startup checks against the candidate sources, minutes before the image exists"
PY=.venv/bin/python
# 2a: every shipped Python file parses. The image smoke finds a syntax error only AFTER a full build;
# ast.parse finds it here in seconds and writes nothing (no __pycache__).
rc=0; "$PY" - "$ROOT" <<'PYEOF' || rc=$?
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
trees = ['delivery-api/scripts/docker', 'delivery-api/src/delivery_api',
         'interview-engine/src/interview_engine', 'spec-generator/src/spec_generator',
         'verification-loop/src']
bad = []
for rel in trees:
    base = root / rel
    if not base.is_dir():
        continue
    for f in sorted(base.rglob('*.py')):
        try:
            ast.parse(f.read_bytes(), filename=str(f))
        except SyntaxError as e:
            bad.append('%s: %s' % (f, e))
if bad:
    print('files that do not parse:')
    for b in bad[:20]:
        print(' ', b)
    sys.exit(1)
print('parse: every shipped Python file parses')
PYEOF
[ "$rc" = 0 ] || abort SRC-PARSE "a shipped Python file does not parse - the image smoke would have found this after a full build" "Nothing was started." "the file and line are named above"
# 2b: every shipped JS asset parses, in MODULE goal. A JS syntax error is found by a
# browser only AFTER deploy (PR #261 shipped exactly that: one stray '}' in identity.js,
# the SPA stuck on "Loading workspace…"). node --check on a .js can silently pick script
# goal and pass a broken module, so check real copies named .mjs. Without node this step
# cannot verify and says so instead of passing silently.
if command -v node >/dev/null 2>&1; then
  JSTMP=$(mktemp -d); rc=0
  for f in "$ROOT"/delivery-api/src/delivery_api/web/*.js "$ROOT"/delivery-api/src/delivery_api/backoffice_web/*.js; do
    [ -f "$f" ] || continue
    j="$JSTMP/$(basename "${f%.js}").mjs"; cp "$f" "$j"
    out=$(node --check "$j" 2>&1) || { echo "  $(basename "$f"):"; echo "$out" | head -6 | sed 's/^/    /'; rc=1; }
  done
  rm -rf "$JSTMP"
  [ "$rc" = 0 ] || abort SRC-JS-PARSE "a shipped JS asset does not parse - a browser would find this only after deploy" "Nothing was started." "the file and line are named above"
  echo "parse: every shipped JS asset parses (module goal)"
else
  echo "   NOTE: node is not installed on this Mac - shipped JS syntax NOT verified here (the pytest gate covers it wherever node exists)"
fi
# 2c: the startup preflight's own regression tests, on this checkout. Both 2026-10-27 preflight
# regressions reached the image through code whose tests passed here - this runs them BEFORE a build.
rc=0; "$PY" -m pytest tests/test_startup_preflight.py -q -p no:cacheprovider || rc=$?
[ "$rc" = 0 ] || abort SRC-PREFLIGHT "the startup preflight's own tests fail on this checkout" "Nothing was started." "run: .venv/bin/python -m pytest tests/test_startup_preflight.py -x   (the first failure is named)"
# 2d: the candidate engine exposes the surface the image's preflight requires. Checked by ast, not
# import: this Mac's delivery venv does not carry the engine's dependencies, but the image does.
rc=0; "$PY" - "$ROOT/interview-engine/src/interview_engine" <<'PYEOF' || rc=$?
import ast, pathlib, sys
base = pathlib.Path(sys.argv[1])
def tree(name):
    try:
        return ast.parse((base / name).read_bytes())
    except OSError:
        print('missing: %s' % (base / name))
        sys.exit(1)
def funcs(t):
    return {n.name for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
http = tree('http_service.py')
has_app = 'create_app' in funcs(http)
has_dsn = any(isinstance(n, ast.Assign)
              and any(getattr(t, 'id', '') == 'SESSIONS_DSN_ENV' for t in n.targets)
              and isinstance(n.value, ast.Constant) and n.value.value == 'INTERVIEW_ENGINE_SESSIONS_DSN'
              for n in ast.walk(http))
pool = tree('pg_pool.py')
build_pool = next((n for n in ast.walk(pool) if isinstance(n, ast.FunctionDef) and n.name == 'build_pool'), None)
has_max = build_pool is not None and 'max_size' in [a.arg for a in build_pool.args.args + build_pool.args.kwonlyargs]
missing = [label for label, ok in (('http_service.create_app', has_app),
                                   ("SESSIONS_DSN_ENV == 'INTERVIEW_ENGINE_SESSIONS_DSN'", has_dsn),
                                   ('pg_pool.build_pool(max_size=)', has_max)) if not ok]
if missing:
    print('engine surface mismatch: ' + ', '.join(missing))
    sys.exit(1)
print('engine surface: create_app, the sessions DSN contract and build_pool(max_size=) are all present')
PYEOF
[ "$rc" = 0 ] || abort SRC-ENGINE "the candidate interview-engine tree does not expose the surface the image's startup preflight requires - the image would refuse to start" "Nothing was started." "the mismatch is named above"
step_ok

fi

step "confirmation"
# Three provider modes. both is the default and composes two independent pins. ollama: the operator's local model at $0 - no key, no vendor pages, the reviewed row
# and ceiling grounded from this Mac's server; heavy on this Mac while it runs. deepseek: the paid managed
# route, nothing local, DeepSeek-only. PREVIEW_EXEC_PROVIDER picks one for scripted runs; unset, the operator
# gets both routes by default; an explicit environment value may narrow them.
# Local manual runs expose both routes unless an operator explicitly narrows them.
# The launch confirmation below still describes live data and paid execution.
PROVIDER=${PREVIEW_EXEC_PROVIDER:-both}
case "$PROVIDER" in deepseek|ollama|both) ;; *) abort PROVIDER "PREVIEW_EXEC_PROVIDER must be deepseek, ollama or both, not '$PROVIDER'" "" "unset PREVIEW_EXEC_PROVIDER";; esac
OLLAMA_MODEL=${PREVIEW_EXEC_OLLAMA_MODEL:-phi4}
OLLAMA_HOST=${OLLAMA_HOST:-http://localhost:11434}
# File-first key is inherited privately, separately from the filtered container env file.
if [ "$PROVIDER" != ollama ]; then
  [ -n "${DEEPSEEK_API_KEY:-}" ] || abort NO-KEY "DEEPSEEK_API_KEY is missing after file-first settings loading" "Nothing was started and the database was not touched." "save DEEPSEEK_API_KEY in the protected preview.env or checkout .env, then retry"
  case "${DEEPSEEK_API_KEY:-}" in *[[:space:]]*) abort KEY-SPACE "DEEPSEEK_API_KEY has a space or line break in it" "" "export only the key, then run again";; esac
  [ "${#DEEPSEEK_API_KEY}" -ge 20 ] || abort KEY-SHORT "DEEPSEEK_API_KEY is too short to be a DeepSeek key" "" "export the whole key, then run again"
fi

echo "ops preview-exec: candidate $IMAGE (built on this Mac, no server access)"
echo "  What it does: runs $IMAGE on http://127.0.0.1:$PORT with execution ON against the LIVE database."
echo "  Also starts a private Back office at http://127.0.0.1:$BACKOFFICE_PREVIEW_PORT; both stop with this script."
if [ "$PROVIDER" = deepseek ]; then
echo "  Provider: DeepSeek only (paid) - no local model server is checked, contacted or needed. The local \$0 Ollama route is the other choice at the provider prompt (or PREVIEW_EXEC_PROVIDER=ollama)."
echo "  Reviewed models, prices and ceilings come from the shared Back office Model pins store."
echo "  No local default or fallback is invented; missing reviewed pins block dispatch."
echo "  The DeepSeek key comes from repo .env first, private preview settings next, then shell; missing keys stop the launcher. Values are never printed."
echo "  Real spend is possible only for a person with an admission row and prepaid credits; anyone else is 'not admitted'."
elif [ "$PROVIDER" = both ]; then
echo "  Providers: both. UI choice per task: paid DeepSeek or reviewed local Ollama default at \$0. No automatic fallback."
echo "  The launcher uses shared reviewed pins; local model work starts only when selected."
echo "  Later local model work runs only when you select Ollama; DeepSeek remains independently available."
else
echo "  Provider: local Ollama model $OLLAMA_MODEL at $OLLAMA_HOST (\$0, nothing paid, no key needed) - heavy on this Mac while it runs; PREVIEW_EXEC_PROVIDER=deepseek or picking 2 selects the paid managed route."
echo "  The reviewed model and context ceiling come from Back office Model pins, not local discovery or the candidate"
echo "  checkouts; every call is still reserved and settled in the shared ledger at \$0. The container reaches"
echo "  the server through host.docker.internal."
fi
echo "  This start is NOT the activation approval for the first real interview: spend limit, cost ceiling, model"
echo "  route and live changes are approved once, separately, before that interview."
prompt 'Waiting for input: Type "yes" (English keyboard) to start, anything else changes nothing:'
A=no; prompt_open && read -r A <&9 || A=${A:-no}; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "" "run ops preview-exec again and type yes"
step_ok

step "settings + grounding"
trap cleanup EXIT INT TERM
umask 077; WORK=$(mktemp -d "${TMPDIR:-/tmp}/requi-preview-exec.XXXXXXXXXX"); chmod 700 "$WORK";
# settings: saved file to a private temp file, never printed, execution ON. The local-preview flag -
# which offers the ollama route - is written only in ollama mode: the route is dead without OLLAMA_BASE_URL,
# and a selectable dead route fails the task start with a connection error instead of being absent.
.venv/bin/python scripts/ops/settings.py delivery-env "$WORK/loaded.env" || abort SETTINGS "cannot prepare private settings"
build_exec_env "$WORK/loaded.env" "$WORK/preview.env" || abort NO-TOKEN "could not make the temporary engine token" "" "send me this output"
# The old saved settings cannot silently opt into paid children. Owner approval is separate.
if [ "$PROVIDER" != ollama ]; then
  echo 'Paid route visibility is not approval: paid dispatch requires an audited Back office expense policy. Customers see credits only.'
  printf '%s\n' DELIVERY_CHILD_EXECUTION=attended-paid DELIVERY_PAID_CHILD_PREVIEW=1 >> "$WORK/preview.env"
else
  printf '%s\n' DELIVERY_CHILD_EXECUTION=local >> "$WORK/preview.env"
fi
if [ "$PROVIDER" != deepseek ]; then
  preview_ollama_env "$WORK/preview.env" "$OLLAMA_MODEL" "$OLLAMA_HOST"
fi
chmod 600 "$WORK/preview.env"
grep -q -i -E '^[A-Z0-9_]*(DEEPSEEK|OPENAI|ANTHROPIC|API_KEY)' "$WORK/preview.env" && abort MODEL-KEY "a model key is in the saved settings file; the temporary container settings must exclude model keys" "" "send the failure output, never your settings values"
# DeepSeek-only means no Ollama dependency anywhere: the local server is never checked, contacted or
# needed, and no Ollama setting may reach this mode's settings. By construction none can (the saved-file
# copy admits only DELIVERY_* and the Ollama lines are written in the ollama branch only); this guard
# keeps that true if either side changes.
if [ "$PROVIDER" = deepseek ]; then
  grep -q -E '^(OLLAMA_[A-Z0-9_]*|DELIVERY_LOCAL_PREVIEW)=' "$WORK/preview.env" && abort OLLAMA-LEAK "an Ollama setting reached the DeepSeek-only preview settings" "Nothing was started." "send me this output (not the settings file)"
fi
[ "$(grep -c '^DELIVERY_CLOUD_EXECUTION=1$' "$WORK/preview.env")" = 1 ] || abort EXEC-OFF "execution is not forced on in the preview-exec settings" "" "send me this output"
[ "$(grep -c '^DELIVERY_PUBLIC_ORIGIN=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's sign-in address is not set to its own local address" "" "send me this output"
[ "$(grep -c '^DELIVERY_ALT_ORIGINS=' "$WORK/preview.env")" = 1 ] && grep -q "^DELIVERY_ALT_ORIGINS=http://localhost:$PORT\$" "$WORK/preview.env" || abort ORIGIN "the preview's second sign-in address is not exactly localhost with its own port" "" "send me this output"
[ "$(grep -c '^DELIVERY_PREVIEW=1$' "$WORK/preview.env")" = 1 ] || abort PREVIEW-FLAG "the preview flag is not set exactly once" "" "send me this output"
# One durable pin store, shared with Back office and the runtime. No separate launch pin generator.
echo '== Model pins: read from the shared store; review changes in Back office > Model pins.'
printf '%s\n' REQUI_PIN_STORE=1 >> "$WORK/preview.env"
case "$PROVIDER" in both) printf '%s\n' REQUI_PIN_PROVIDERS=deepseek,ollama >> "$WORK/preview.env";; *) printf 'REQUI_PIN_PROVIDERS=%s\n' "$PROVIDER" >> "$WORK/preview.env";; esac
# Public config is resolved inside the runtime using its restricted DB identity.
# Empty/unavailable pins keep execution closed, but the operator UI must still boot for repair.
: > "$WORK/cloud-models.env"
step_ok

step "build (skipped when the exact candidate tag is already on this Mac)"
HAVE=1; docker image inspect "$IMAGE" >/dev/null 2>&1 || HAVE=0
if [ "$HAVE" = 0 ]; then
  echo "== building $IMAGE on this Mac (no second yes: the one typed in step 3 covers the build; the build publishes the tag only after its own smoke check passes)"
  ( umask 022; REQUI_BUILD_YES=1 bash scripts/docker/build-local-image.sh ) || abort BUILD "the local build did not finish" "Nothing was started on this Mac. The build script removes a candidate image that fails its smoke check." "send me this output"
else
  echo "== the exact candidate tag is already on this Mac - nothing to build (the smoke in step 6 decides whether it may be reused)"
fi
step_ok

step "image smoke"
if [ "$FAST_CURRENT" = 1 ]; then
  echo "SKIPPED image smoke: --fast, exact image identity verified"
elif [ "$HAVE" = 1 ]; then
  echo "== the image is already on this Mac: checking that it passes the smoke check before it is reused"
  rc=0
  SMOKE_OUT=$(bash scripts/docker/smoke-local-image.sh "$IMAGE" "$PLATFORM" 2>&1) || rc=$?
  [ -z "$SMOKE_OUT" ] || printf '%s\n' "$SMOKE_OUT"
  if [ "$rc" = 0 ]; then
    echo "== reuse smoke passed"
  else
    [ -n "$SMOKE_OUT" ] || echo "== the smoke container produced no output at all (docker exit code $rc): it was killed before it could print - a signal kill, commonly the Docker Desktop VM running out of memory, leaves no output"
    if docker rmi "$IMAGE" >/dev/null 2>&1; then
      echo "== that image failed (exit code $rc) and its tag was removed (that exact tag only); building it again (its own smoke gates the new tag)"
      ( umask 022; REQUI_BUILD_YES=1 bash scripts/docker/build-local-image.sh ) || abort BUILD "the local build did not finish" "Nothing was started on this Mac. The build script removes a candidate image that fails its smoke check." "send me this output"
    else
      abort IMAGE-SMOKE "the existing image failed the smoke check (exit code $rc) and could not be removed" "" "send me the output above"
    fi
  fi
else
  echo "== the fresh build already ran this smoke on the candidate before publishing the tag - nothing to redo"
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || abort IMAGE-MISSING "the image $IMAGE is not on this Mac after the build" "" "docker image ls requi-full   (send me this output)"
ARCH=$(docker image inspect --format '{{.Architecture}}' "$IMAGE" 2>/dev/null || true)
[ "$ARCH" = "$EXPECTED_ARCH" ] || abort ARCH "the image is $ARCH, expected $EXPECTED_ARCH (this Mac's local build)" "" "send me this output"
step_ok

step "database + startability"
# can this Mac reach the database? (prints only OK or the error type, never the address)
DBCHECK='import os,sys
try:
    import psycopg
    psycopg.connect(os.environ["DELIVERY_DATABASE_URL"],connect_timeout=10).execute("select 1")
    print("DBOK")
except Exception as e:
    print("DBFAIL",type(e).__name__)'
OUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" "$IMAGE" python -c "$DBCHECK" 2>&1 | scrub | tail -1 || true)
[ "$OUT" = DBOK ] || abort NO-DB "this Mac cannot open the database ($OUT)" "" "check your network or VPN; if it says OperationalError the database may not accept this Mac's address: send me this line"
# the image's OWN startup rules, run on these exact settings plus the pinned prices and the key. Prints problem names only, never a value.
CONTRACT='import os,sys;sys.path.insert(0,"/app");import full_entry;p=full_entry.check_env(os.environ);print("CONTRACT-OK" if not p else "CONTRACT-PROBLEMS");[print("problem:",x) for x in p]'
KEYPASS=(); [ "$PROVIDER" != ollama ] && KEYPASS=(-e DEEPSEEK_API_KEY)
COUT=$(docker run --rm --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --env-file "$WORK/preview.env" --env-file "$WORK/cloud-models.env" ${KEYPASS[@]+"${KEYPASS[@]}"} "$IMAGE" python -c "$CONTRACT" 2>&1 | scrub | tail -8 || true)
echo "$COUT" | grep -qx CONTRACT-OK || abort NOT-STARTABLE "the image would not start with these settings: $(echo "$COUT" | grep '^problem:' | tr '\n' ' ')" "" "send me this output"
step_ok

step "start + readiness"
# Run with persistent local data. Never remove this volume in cleanup/rebuild.
# Existing old temporary preview data is not copied or deleted by this change.
docker run -d --name "$NAME" --platform "$PLATFORM" --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 3g --memory-swap 3g \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 -v "$DATA_VOLUME":/data --env-file "$WORK/preview.env" --env-file "$WORK/cloud-models.env" \
  ${KEYPASS[@]+"${KEYPASS[@]}"} --add-host host.docker.internal:host-gateway -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null || abort RUN "the container did not start" "" "docker logs $NAME   (send me this output)"
STARTED=1
OK=0
for i in $(seq 1 "${PREVIEW_WAIT_TRIES:-90}"); do
  if curl -fs --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then OK=1; break; fi
  docker ps --format '{{.Names}}' | grep -qx "$NAME" || break
  sleep "${PREVIEW_POLL:-2}"
done
if [ "$OK" != 1 ]; then echo "== the container did not become healthy; last log lines:"; docker logs --tail 30 "$NAME" 2>&1 | scrub; abort NOT-HEALTHY "the preview did not start" "" "send me the log lines above"; fi
# The flag/key check verifies configuration, not actual gateway readiness.
# Application startup status is reported truthfully in the final summary below.
PROBE='import os,re;print("EXEC="+os.environ.get("DELIVERY_CLOUD_EXECUTION","unset"));print("KEYS="+str(len([k for k in os.environ if re.search("DEEPSEEK|OPENAI|ANTHROPIC|API_KEY",k,re.I)])))'
EXECOUT=$(docker exec "$NAME" python -c "$PROBE" 2>/dev/null || true)
EXPECTED_KEYS=1; [ "$PROVIDER" = ollama ] && EXPECTED_KEYS=0
echo "$EXECOUT" | grep -qx 'EXEC=1' && echo "$EXECOUT" | grep -qx "KEYS=$EXPECTED_KEYS" || abort EXEC-UNVERIFIED "the running container's environment does not confirm execution on with the expected provider key count ($EXPECTED_KEYS)" "" "send me this output"
step_ok

step "browser smoke: the real page boots clean - only named, expected anonymous 401s tolerated"
# Anonymous and read-only BY CONSTRUCTION: the preview runs on the live database with real
# sign-in, so this step never submits a form and never signs in - it only loads / exactly
# like a visitor. An anonymous cloud boot is NOT console-error-free by design: the tenant
# boundary 401s the page's own pre-sign-in probes. The classifier (scripts/ops/browser_smoke.py,
# tested in tests/test_preview_browser_smoke.py) tolerates only those named probes and fails on
# anything else, naming the URL Chromium's console line omits. The fake-login half lives in the
# hermetic pytest (tests/test_ui_playwright.py::test_boot_smoke_zero_console_errors_anonymous_and_after_fake_login),
# which runs against a throwaway test database, never the live one.
if [ "$FAST_CURRENT" = 1 ]; then
  echo "SKIPPED browser smoke: --fast, exact image identity verified; UI boot not browser-verified this run"
elif .venv/bin/python -c "import playwright.sync_api" 2>/dev/null; then
  rc=0; BROWSER_OUT=$(.venv/bin/python scripts/ops/browser_smoke.py "http://127.0.0.1:$PORT" 2>&1 | scrub) || rc=$?
  [ -z "$BROWSER_OUT" ] || printf '%s\n' "$BROWSER_OUT"
  case "$rc" in
    0) ;;
    2) echo "   NOTE: chromium is not installed for the checkout venv - boot NOT browser-verified; run: .venv/bin/python -m playwright install chromium";;
    *) KEEP_FOR_INSPECTION=1; abort BROWSER-SMOKE "the preview page does not boot clean in a real browser (the errors are named above)" "The preview stays up for inspection; its UI may be broken." "send me this output";; esac
else
  echo "   NOTE: playwright is not in the checkout venv - boot NOT browser-verified; run: .venv/bin/python -m pip install -e '.[dev]' && .venv/bin/python -m playwright install chromium"
fi
step_ok
echo
if start_backoffice; then
  echo "BACK OFFICE READY: http://127.0.0.1:$BACKOFFICE_PREVIEW_PORT (stops with this script)"
else
  warn 'WARNING: optional Back office unavailable; preview remains usable. No unrelated listener was reused or stopped.'
fi
# Re-read after browser/Back office checks: health may arrive before startup
# status. Missing status is UNVERIFIED, never evidence for OPEN or CLOSED.
EXECUTION_STATUS=$(docker logs "$NAME" 2>&1 | scrub | awk '
  /^Cloud execution: OPEN([ (]|$)/ {state="OPEN"}
  /^Cloud execution: CLOSED([ -]|$)/ {state="CLOSED"}
  END {print (state ? state : "UNVERIFIED")}' )
if [ "$EXECUTION_STATUS" = OPEN ]; then
  echo "PREVIEW-EXEC READY: http://127.0.0.1:$PORT (execution OPEN confirmed by application startup; provider: $PROVIDER; per-call admission and credits still required)"
else
  warn "PREVIEW UI READY: http://127.0.0.1:$PORT (WARNING: execution $EXECUTION_STATUS; provider: $PROVIDER). Review Back office > Model pins and Debug before starting a task."
fi
echo "Running build: $(printf '%s' "$REPOS" | sed 's/=/ /g; s/,/, /g')"
echo "Reminder: the first real interview still needs its own one-time approval (spend limit, cost ceiling, model route, live changes)."
echo "Press Ctrl-C here when you are done."
docker logs -f "$NAME" 2>&1 | scrub || true
