#!/usr/bin/env bash
# Requi.re local EXECUTION preview: the candidate image on this Mac, against the LIVE database, with execution ON.
# Same admitted in-process host and the same per-call admission and billing ledger as the cloud. A real interview
# run here spends real money for a person who has an admission row and prepaid credits; anyone else is answered
# 'not admitted'. Starting this preview is NOT the activation approval for the first real interview: the spend
# limit, cost ceiling, model route and live changes are approved once, separately, before that interview.
# Settings: the file saved on this Mac by scripts/ops/preview-provision.sh (~/.config/requi/preview.env), plus
# a fresh pin of the reviewed model row: the local Ollama server in ollama mode (scripts/ops/pin-local.py, $0,
# nothing paid), or the two public DeepSeek pages in deepseek mode (scripts/ops/pin.py, the exact step
# ops go-live runs; public GETs only, never a paid call). DeepSeek keys load from private preview.env, then
# checkout .env, then the shell. Only missing values ask for hidden terminal input; saving is opt-in. The key
# passes privately to the container, never in argv, logs or the temporary Docker env file. Deepseek mode
# is DeepSeek-ONLY: the local Ollama server is never checked, contacted or needed, and no Ollama setting may
# reach its settings. If any prerequisite is missing this stops with a clear message; it never falls back to
# another model route. Ctrl-C removes the container and the temp folder.
#
# Fail-fast: 8 numbered steps, cheapest and most likely first, each printing its elapsed time. The plan with
# expected durations prints before step 1. There are exactly two questions: the provider pick (only when
# PREVIEW_EXEC_PROVIDER is unset) and one typed yes. The build script's own yes is covered by that yes
# (REQUI_BUILD_YES=1); there is no key prompt - a missing DeepSeek key is a loud stop.
set -euo pipefail
PORT=${PREVIEW_EXEC_PORT:-8001}
NAME=requi-preview-exec; WORK=""; STARTED=0; KEEP_FOR_INSPECTION=0
DATA_VOLUME=${PREVIEW_DATA_VOLUME:-requi-preview-data}
. "$(dirname "$0")/lib.sh"; ops_start preview-exec "$0" --venv
REPO=$PWD

scrub(){ sed -E 's#(postgres(ql)?://)[^ ]*#\1[hidden]#g; s#sk-[A-Za-z0-9_-]+#sk-[hidden]#g'; }
cleanup(){
  rc=$?; trap - EXIT INT TERM
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
  echo; echo "ABORTED [$1]: $2"
  if [ "$1" = BROWSER-SMOKE ]; then
    echo "browser errors repeated for inspection (anonymous smoke, no sign-in):"
    [ -z "${BROWSER_OUT:-}" ] || printf '%s\n' "$BROWSER_OUT"
  fi
  [ -z "$STEP_NAME" ] || echo "Step $STEP_N/$STEPS_TOTAL '$STEP_NAME' failed after $((SECONDS-STEP_START))s."
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
      | grep -v -E '^(DELIVERY_CLOUD_EXECUTION|DELIVERY_COOKIE_SECURE|DELIVERY_PREVIEW|DELIVERY_LOCAL_PREVIEW|DELIVERY_METRICS_DB|DELIVERY_HOST|DELIVERY_PORT|DELIVERY_CONTROL_PLANE_ONLY|DELIVERY_PUBLIC_ORIGIN|DELIVERY_ALT_ORIGINS)=' >> "$2" || true
    # The engine token is a password between the two processes inside the preview container only. It is made fresh here (never the live one, which the allowlist drops).
    tok=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); [ ${#tok} = 48 ] || exit 1
    # REQUI_EXPECTED_REPOS pins the build this launcher asked for, so the image's own build-staleness
    # check can compare it against the REQUI_BUILD_REPOS the image was built with (preview.sh pins it
    # too, but this script builds its env itself - that pin never covered this path).
    printf '%s\n' "INTERVIEW_ENGINE_TOKEN=$tok" DELIVERY_CONTROL_PLANE_ONLY=0 DELIVERY_CLOUD_EXECUTION=1 DELIVERY_COOKIE_SECURE=0 DELIVERY_PREVIEW=1 DELIVERY_METRICS_DB=/data/requi-re/run_metrics.db DELIVERY_HOST=0.0.0.0 DELIVERY_PORT=8000 "DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:$PORT" "DELIVERY_ALT_ORIGINS=http://localhost:$PORT" "REQUI_EXPECTED_REPOS=$REPOS" >> "$2" ) || return 1
  chmod 600 "$2"
}

cat <<'PLAN'
Plan (8 steps, fail fast - cheapest and most likely first; every step prints its elapsed time, every failure names its step):
  1 inputs and environment (~5s): tools, Docker, port, saved settings, the four checkouts at origin/main, LIVE DB schema + app role readiness
  2 source smoke (~15s): the image's startup checks against the candidate sources - syntax parse, the preflight's own tests, the engine surface
  3 confirmation (you): provider pick if unset, DeepSeek key presence (deepseek only), what will run, one typed yes
  4 settings + grounding (~10s deepseek; ~1-5 min ollama: it loads the model on this Mac)
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
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then abort PORT-BUSY "something already listens on 127.0.0.1:$PORT" "" "stop it, or: PREVIEW_EXEC_PORT=8011 ops preview-exec"; fi
docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && abort LEFTOVER "a container named $NAME is left over from an earlier run" "" "docker rm -f $NAME"
MACHINE=$(uname -m); case "$MACHINE" in arm64|aarch64) NATIVE=arm64;; x86_64|amd64) NATIVE=amd64;; *) abort ARCH "unknown machine architecture $MACHINE";; esac
EXPECTED_ARCH=${PREVIEW_ARCH:-$NATIVE}; case "$EXPECTED_ARCH" in arm64|amd64) ;; *) abort ARCH "PREVIEW_ARCH must be arm64 or amd64, not '$EXPECTED_ARCH'" "" "unset PREVIEW_ARCH";; esac
PLATFORM=linux/$EXPECTED_ARCH
# the settings saved on this Mac by ops preview-provision (one time), never printed
REQUIRED=DELIVERY_DATABASE_URL,DELIVERY_SUPABASE_URL,DELIVERY_SUPABASE_ANON_KEY
[ "${PREVIEW_EXEC_PROVIDER:-both}" = ollama ] || REQUIRED="$REQUIRED,DEEPSEEK_API_KEY"
ops_settings "$REQUIRED"
# the candidate: the four checkouts at origin/main, exactly as deploy-full takes them
[ -f scripts/docker/build-local-image.sh ] || abort NO-FILE "scripts/docker/build-local-image.sh is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"
candidate_tag
PINFILES="scripts/ops/pin.py src/delivery_api/vendor_sources.py src/delivery_api/retail_pricing.py scripts/ops/pin-local.py src/delivery_api/ollama_usage.py src/delivery_api/local_route.py src/delivery_api/provider_network.py"
for f in $PINFILES src/delivery_api/production_gateway.py; do [ -f "$f" ] || abort NO-FILE "$f is missing on main" "" "git -C $PWD log --oneline -5   (is main pulled?)"; done
# The venv must carry what the steps ahead import - the pool redesign added psycopg-pool and older
# venvs predate it (ops_venv installs -e . without extras, so even a fresh ops venv lacks it). Fail
# here with the exact install line, not inside a step-2 pytest import traceback.
rc=0; .venv/bin/python scripts/ops/venv_check.py || rc=$?
[ "$rc" = 0 ] || abort VENV-DEPS "the checkout's .venv is missing declared dependencies" "Nothing was started." "run the exact pip line printed above, then run ops preview-exec again"
# Cheap read-only schema proof before local model grounding or image build.
.venv/bin/python scripts/ops/schema-readiness.py || abort SCHEMA-NOT-READY "live schema or app SET memberships are behind this checkout" "No database changes; nothing built or started." "run python3 scripts/owner/apply-pg-cutover.py . for dry review; do not send credentials"
step_ok

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
  for f in "$ROOT"/delivery-api/src/delivery_api/web/*.js; do
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
if [ "$PROVIDER" = deepseek ]; then
echo "  Provider: DeepSeek only (paid) - no local model server is checked, contacted or needed. The local \$0 Ollama route is the other choice at the provider prompt (or PREVIEW_EXEC_PROVIDER=ollama)."
echo "  The reviewed row and ceiling are grounded from the two public DeepSeek pages (public GETs, nothing paid),"
echo "  with the configured safety ceiling 128000 tokens per call / 16384 max output."
echo "  The DeepSeek key comes from this shell's environment and is passed to the container without touching disk."
echo "  Real spend is possible only for a person with an admission row and prepaid credits; anyone else is 'not admitted'."
elif [ "$PROVIDER" = both ]; then
echo "  Providers: both. UI choice per task: paid DeepSeek or local Ollama $OLLAMA_MODEL at \$0. No automatic fallback."
echo "  Grounding loads the local model once (heavy on this Mac for 1-5 min); DeepSeek pin reads public pages only."
echo "  Later local model work runs only when you select Ollama; DeepSeek remains independently available."
else
echo "  Provider: local Ollama model $OLLAMA_MODEL at $OLLAMA_HOST (\$0, nothing paid, no key needed) - heavy on this Mac while it runs; PREVIEW_EXEC_PROVIDER=deepseek or picking 2 selects the paid managed route."
echo "  The reviewed row and context ceiling are grounded from this Mac's own Ollama server and the candidate"
echo "  checkouts; every call is still reserved and settled in the shared ledger at \$0. The container reaches"
echo "  the server through host.docker.internal."
fi
echo "  This start is NOT the activation approval for the first real interview: spend limit, cost ceiling, model"
echo "  route and live changes are approved once, separately, before that interview."
printf 'Type yes (English keyboard) to start, anything else changes nothing: '
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
# reviewed prices and limits. deepseek: the exact pin step ops go-live runs (public GETs, nothing paid).
# ollama: grounded from this Mac's own server and the candidate checkouts (local HTTP, nothing paid) -
# this LOADS the model once so the server reports the context window it will actually use.
if [ "$PROVIDER" = both ]; then
  mkdir "$WORK/paid-pin" "$WORK/local-pin"
  echo "== both: public DeepSeek pin, then local Ollama grounding (loads the model once; no paid call)"
  rc=0
  PYTHONPATH=src .venv/bin/python "$(dirname "$0")/pin.py" "$WORK/paid-pin" > "$WORK/pin-out.txt" 2>&1 || rc=$?
  if [ "$rc" = 0 ]; then
    PYTHONPATH=src REQUI_REPOS_ROOT="$ROOT" PREVIEW_EXEC_OLLAMA_MODEL="$OLLAMA_MODEL" OLLAMA_HOST="$OLLAMA_HOST" .venv/bin/python "$(dirname "$0")/pin-local.py" "$WORK/local-pin" "$ROOT" >> "$WORK/pin-out.txt" 2>&1 || rc=$?
  fi
  if [ "$rc" = 0 ]; then
    PYTHONPATH=src .venv/bin/python "$(dirname "$0")/combine-provider-pins.py" "$WORK" "$WORK/paid-pin" "$WORK/local-pin" >> "$WORK/pin-out.txt" 2>&1 || rc=$?
  fi
elif [ "$PROVIDER" = ollama ]; then
echo "== grounding the local Ollama row and context ceiling (local HTTP, \$0, nothing paid)"
echo "   What this does: checks the server, loads the model once (this Mac works hard for ~1-5 min - fan noise"
echo "   is normal), reads the context window the server actually loaded, caps it by the largest stage output in"
echo "   the candidate engine/generator sources, and writes the reviewed \$0 row + ceiling for the container."
rc=0; PYTHONPATH=src REQUI_REPOS_ROOT="$ROOT" PREVIEW_EXEC_OLLAMA_MODEL="$OLLAMA_MODEL" OLLAMA_HOST="$OLLAMA_HOST" .venv/bin/python "$(dirname "$0")/pin-local.py" "$WORK" "$ROOT" > "$WORK/pin-out.txt" 2>&1 || rc=$?
else
echo "== reading the two DeepSeek pages (public GET, nothing paid)"
rc=0; PYTHONPATH=src .venv/bin/python "$(dirname "$0")/pin.py" "$WORK" > "$WORK/pin-out.txt" 2>&1 || rc=$?
fi
if [ "$rc" != 0 ]; then
  tail -15 "$WORK/pin-out.txt"
  case "$(tail -1 "$WORK/pin-out.txt")" in PIN-FAIL*)
    if [ "$PROVIDER" = ollama ]; then abort PIN "see the line above" "" "follow the fix the PIN-FAIL line above names, then run this again; if it still refuses, send me this output"
    else abort PIN "see the line above" "" "send me this output; the page or the parser changed, and I have to look before anything is pinned"; fi;;
    *) abort PIN-CRASH "the pin step crashed" "" "send me this whole output";; esac
fi
cat "$WORK/pin-out.txt"
[ -s "$WORK/cloud-models.env" ] || abort PIN-EMPTY "the pin step wrote no settings" "" "send me this output"
grep -q '^VMODEL_REVIEWED_MODEL_PRICES=.' "$WORK/cloud-models.env" && grep -q '^VMODEL_REVIEWED_CONTEXT_CEILING=.' "$WORK/cloud-models.env" || abort PIN-PARTIAL "the pinned settings are incomplete" "" "send me this output (not the files)"
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
if [ "$HAVE" = 1 ]; then
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
# readiness needs POSITIVE evidence: the app's own startup line says OPEN, and the running container's own
# environment confirms execution 1 with exactly one provider key variable (its value is never printed).
LOGS=$(docker logs "$NAME" 2>&1 | scrub)
echo "$LOGS" | grep -q 'Cloud execution: OPEN' || abort EXEC-UNKNOWN "the app did not report 'Cloud execution: OPEN' at startup" "" "send me this output"
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
if .venv/bin/python -c "import playwright.sync_api" 2>/dev/null; then
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
echo "PREVIEW-EXEC READY: http://127.0.0.1:$PORT   (execution OPEN, live database, real sign-in, provider: $PROVIDER)"
[ "$PROVIDER" = both ] && echo "Both routes admitted: choose DeepSeek or Ollama in the UI. Local is zero-cost; DeepSeek uses credits. No fallback."
[ "$PROVIDER" = ollama ] && echo "Local Ollama route: $OLLAMA_MODEL at \$0; every call is ledgered. The managed DeepSeek route is not configured in this mode."
echo "Spend is possible only for a person with an admission row and prepaid credits."
echo "Running build: $(printf '%s' "$REPOS" | sed 's/=/ /g; s/,/, /g')"
echo "Reminder: the first real interview still needs its own one-time approval (spend limit, cost ceiling, model route, live changes)."
echo "Press Ctrl-C here when you are done."
docker logs -f "$NAME" 2>&1 | scrub || true
