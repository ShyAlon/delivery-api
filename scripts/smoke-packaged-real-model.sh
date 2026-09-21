#!/usr/bin/env bash
# Build the real distribution, launch it, and drive one HTTP-only interview.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=full; MODEL_ROUTE=ollama; FRESH=0
for arg in "$@"; do
  case "$arg" in
    --launcher-only) MODE=launcher ;;
    --fresh) FRESH=1 ;;
    --model-route=ollama) MODEL_ROUTE=ollama ;;
    --model-route=deepseek) MODEL_ROUTE=deepseek ;;
    *) echo "Usage: $0 [--launcher-only] [--fresh] [--model-route=ollama|--model-route=deepseek]" >&2; exit 2 ;;
  esac
done
progress(){ printf '%s | %-22s | %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" "$2"; }
export REQUI_RE_REPOS_ROOT="${REQUI_RE_REPOS_ROOT:-$(cd "$ROOT/.." && pwd)}"
SHARED_ENV="$REQUI_RE_REPOS_ROOT/.env"
if [ -f "$SHARED_ENV" ]; then
  set -a
  # shellcheck disable=SC1090 - this is the user's local untracked Requi.re config.
  . "$SHARED_ENV"
  set +a
  progress config.loaded "Loaded shared model configuration from $SHARED_ENV"
fi
if [ "$MODEL_ROUTE" = deepseek ]; then
  [ -n "${DEEPSEEK_API_KEY:-}" ] || { echo "Error: DeepSeek smoke needs DEEPSEEK_API_KEY in $SHARED_ENV." >&2; echo "Likely cause: the shared model config has not been switched to DeepSeek." >&2; echo "Next: run ./vmodel-spec-app/scripts/switch-model.sh deepseek, then retry." >&2; exit 1; }
  [ "${INTERVIEW_ENGINE_MODEL_PROBING:-}" = deepseek:* ] || { echo "Error: interview probing is not routed to DeepSeek in $SHARED_ENV." >&2; echo "Likely cause: only the API key is present; the shared model routes still name another provider." >&2; echo "Next: run ./vmodel-spec-app/scripts/switch-model.sh deepseek, then retry." >&2; exit 1; }
  [ "${INTERVIEW_ENGINE_MODEL_DISTILLATION:-}" = deepseek:* ] || { echo "Error: interview distillation is not routed to DeepSeek in $SHARED_ENV." >&2; echo "Likely cause: the shared model switch is incomplete; ConOps follows distillation." >&2; echo "Next: run ./vmodel-spec-app/scripts/switch-model.sh deepseek, then retry." >&2; exit 1; }
  progress config.route "DeepSeek key and interview routes are configured (secret not printed)"
fi
export REQUI_RE_DIST_DIR="${REQUI_RE_DIST_DIR:-$ROOT/dist/requi-re-smoke}"
export REQUI_RE_SMOKE_STATE_DIR="${REQUI_RE_SMOKE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/requi-re-smoke}"
if [ "$FRESH" = 1 ]; then progress state.reset "Removing only the saved smoke run before a clean full cycle"; rm -rf "$REQUI_RE_SMOKE_STATE_DIR"; fi
mkdir -p "$REQUI_RE_SMOKE_STATE_DIR/data" "$REQUI_RE_SMOKE_STATE_DIR/artifacts"
progress build.start "Building a clean distribution from all six repositories"
"$ROOT/scripts/build-dist.sh" --clean
progress build.complete "Distribution built at $REQUI_RE_DIST_DIR"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/requi-re-smoke.XXXXXX")"
cleanup(){
  status=$?
  kill "${LAUNCHER_PID:-}" 2>/dev/null || true; wait "${LAUNCHER_PID:-}" 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    progress smoke.failed "Smoke stopped with exit $status; diagnostic deviations follow" >&2
    echo "=== INTERVIEW ENGINE (last 200 lines) ===" >&2; tail -200 "$REQUI_RE_SMOKE_STATE_DIR/data/requi-re/interview-engine.log" >&2 || true
    echo "=== DELIVERY LAUNCHER (last 200 lines) ===" >&2; tail -200 "$TMP/launcher.log" >&2 || true
    runlog=$(sed -n 's/^Server log: //p' "$TMP/launcher.log" | tail -1)
    if [ -n "$runlog" ]; then echo "=== SERVER RUN LOG (last 200 lines): $runlog ===" >&2; tail -200 "$runlog" >&2 || true; fi
    stage_dir="$REQUI_RE_SMOKE_STATE_DIR/data/requi-re/logs/stages"
    if [ -d "$stage_dir" ]; then
      for stage_log in "$stage_dir"/*.log; do [ -f "$stage_log" ] || continue; echo "=== STAGE $(basename "$stage_log") (last 120 lines) ===" >&2; tail -120 "$stage_log" >&2 || true; done
    fi
    echo "Full smoke logs preserved at: $TMP" >&2
  else progress smoke.complete "Packaged real-model interview passed"; rm -rf "$TMP"; fi
  exit "$status"
}
trap cleanup EXIT
export XDG_DATA_HOME="$REQUI_RE_SMOKE_STATE_DIR/data" VMODEL_ARTIFACTS_DIR="$REQUI_RE_SMOKE_STATE_DIR/artifacts" PORT="${REQUI_RE_SMOKE_PORT:-18080}" INTERVIEW_ENGINE_PORT="${REQUI_RE_SMOKE_ENGINE_PORT:-18010}"
export DELIVERY_BASIC_AUTH_ENABLED=0 DELIVERY_COOKIE_SECURE=0
export INTERVIEW_ENGINE_TOKEN="${INTERVIEW_ENGINE_TOKEN:-requi-re-packaged-smoke-token}"
progress launcher.start "Starting packaged delivery and authenticated interview service"
"$REQUI_RE_DIST_DIR/run-require.sh" >"$TMP/launcher.log" 2>&1 & LAUNCHER_PID=$!
"$REQUI_RE_DIST_DIR/runtime/bin/python" - "$PORT" "$INTERVIEW_ENGINE_PORT" "$INTERVIEW_ENGINE_TOKEN" "$MODE" "$MODEL_ROUTE" "$REQUI_RE_SMOKE_STATE_DIR/checkpoint.json" <<'PY'
import datetime,json,sys,time,urllib.error,urllib.request
from delivery_api.smoke import load_smoke_checkpoint,save_smoke_checkpoint,typed_smoke_answer
port,engine_port,token,mode,route,checkpoint_path=sys.argv[1:]
base=f'http://127.0.0.1:{port}';engine=f'http://127.0.0.1:{engine_port}'
def progress(step,message):print(f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')} | {step:<22} | {message}",flush=True)
def call(method,url,payload=None,headers=None,expected=None):
 data=None if payload is None else json.dumps(payload).encode()
 req=urllib.request.Request(url,data=data,method=method,headers={'Content-Type':'application/json',**(headers or {})})
 try:
  with urllib.request.urlopen(req,timeout=1800) as r: status=r.status;raw=r.read()
 except urllib.error.HTTPError as e: status=e.code;raw=e.read()
 text=raw.decode('utf-8','replace')
 try:body=json.loads(text) if text else {}
 except json.JSONDecodeError:body=text
 if expected is not None and status!=expected:
  request_body=payload if payload is not None else None
  raise RuntimeError(f'{method} {url}: expected HTTP {expected}, got HTTP {status}; request={request_body!r}; response={body!r}')
 if not isinstance(body,(dict,list)):
  request_body=payload if payload is not None else None
  raise RuntimeError(f'{method} {url}: HTTP {status} returned non-JSON; request={request_body!r}; response={body!r}')
 return status,body
for _ in range(120):
 try:
  if call('GET',base+'/health')[0]==200:break
 except Exception:pass
 time.sleep(.25)
else:raise RuntimeError('packaged launcher did not become healthy')
progress('launcher.healthy','Delivery health check passed')
# Prove the service boundary itself is authenticated.
call('GET',engine+'/v1/sessions/not-a-session',expected=401)
call('GET',engine+'/v1/sessions/not-a-session',headers={'X-Interview-Engine-Token':token},expected=404)
progress('service.authenticated','Interview HTTP service rejects missing auth and accepts the shared token')
if mode=='launcher':sys.exit(0)
checkpoint=load_smoke_checkpoint(checkpoint_path)
if checkpoint and checkpoint['model_route']==route:
 task={'task_id':checkpoint['task_id']};answers=int(checkpoint['answers'])
 status,state=call('GET',base+f"/api/tasks/{task['task_id']}")
 if status!=200:checkpoint=None
 elif checkpoint['status']=='complete':
  inp=state['input_state']
  if inp.get('process_live') or inp.get('decision'):
   raise RuntimeError(f"completed smoke checkpoint disagrees with live task {task['task_id']}")
  session=state['engine_session_id'];artifact_root=state['artifact_projection']['root']
  progress('interview.resume',f"Reusing completed task {task['task_id']} with {answers} accepted typed inputs")
  print(json.dumps({'result':'PASS','task_id':task['task_id'],'session_id':session,'answers':answers,'artifact_root':artifact_root,'transport':'HTTP','model_route':route,'resumed':True}))
  sys.exit(0)
 else:
  progress('interview.resume',f"Resuming task {task['task_id']} after {answers} accepted typed inputs")
if not checkpoint or checkpoint.get('model_route')!=route:
 progress('interview.create','Creating smoke task through delivery HTTP API')
 _,task=call('POST',base+'/api/tasks',{'title':f'Packaged real-model smoke {int(time.time())}','brief':'A calm shared household task board that helps two adults assign chores, see due dates, and confirm completion.'},expected=201)
 progress('interview.start',f"Starting real {route} interview for task {task['task_id']}")
 call('POST',base+f"/api/tasks/{task['task_id']}/start",{'model_route':route},expected=200)
 answers=0;save_smoke_checkpoint(checkpoint_path,{'task_id':task['task_id'],'model_route':route,'answers':answers,'last_input_id':None,'status':'active'})
 progress('interview.waiting','First typed input is ready')
for _ in range(80):
 _,state=call('GET',base+f"/api/tasks/{task['task_id']}",expected=200);inp=state['input_state'];decision=inp.get('decision')
 if decision:
  kind=decision['kind'];value=typed_smoke_answer(decision)
  progress('interview.answer',f"Submitting typed input {answers+1} (kind={kind}, rationale_required={bool(decision.get('rationale_required'))})")
  call('POST',base+f"/api/tasks/{task['task_id']}/answer",{'answer':value,'decision_id':decision['input_id']},expected=200);answers+=1
  save_smoke_checkpoint(checkpoint_path,{'task_id':task['task_id'],'model_route':route,'answers':answers,'last_input_id':decision['input_id'],'status':'active'})
  progress('interview.advanced',f"Typed input {answers} accepted");continue
 if answers and not inp.get('process_live'):
  session=state['engine_session_id'];artifact_root=state['artifact_projection']['root']
  save_smoke_checkpoint(checkpoint_path,{'task_id':task['task_id'],'model_route':route,'answers':answers,'last_input_id':None,'status':'complete'})
  progress('interview.complete',f"Interview completed after {answers} typed inputs")
  print(json.dumps({'result':'PASS','task_id':task['task_id'],'session_id':session,'answers':answers,'artifact_root':artifact_root,'transport':'HTTP','model_route':route}))
  sys.exit(0)
 time.sleep(.5)
raise RuntimeError('real-model interview did not reach typed completion')
PY
progress kpi.start "Running packaged-path process-control KPI"
# Static proof applies to the exact source installed into the distribution.
"${REQUI_RE_BUILD_PYTHON:-python3}" -m pytest -q "$ROOT/tests/test_process_control_kpi.py"
