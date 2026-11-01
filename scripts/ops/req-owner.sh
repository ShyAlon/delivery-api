#!/usr/bin/env bash
# Invoked by the existing req runner AFTER clean-main synchronization.
set -euo pipefail
cd "$(dirname "$0")/../.."
fail(){ printf 'REQ ABORTED: %s No owner step was run.\n' "$*" >&2; exit 1; }
command=${1:-}; shift
apply=0; email=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) apply=1;shift;;
    --email) [ "$#" -ge 2 ] || fail '--email needs a value.';email=$2;shift 2;;
    *) fail "unsupported argument $1";;
  esac
done
[ -z "$email" ] || [ "$command" = preflight ] || fail '--email is only for preflight.'
[ -x .venv/bin/python ] || fail 'missing .venv/bin/python; install current checkout dependencies first.'
.venv/bin/python -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)' || fail 'Python 3.11+ required in .venv.'
[ -f scripts/ops/venv_check.py ] || fail 'missing dependency gate; pull current main.'
.venv/bin/python scripts/ops/venv_check.py || fail 'dependencies missing; run the exact pip repair line above.'
envfile="$HOME/.config/requi/preview.env"
case "$command" in
 locks) target=scripts/owner/locks.py;;
 migrate) target=scripts/owner/apply-pg-cutover.py;;
 policy) target=scripts/ops/confirmed-expense-policy.sh;;
 preflight) target=scripts/ops/paid-child-preflight.py;;
 force-stop) target=scripts/owner/force-stop-all.py;;
 stop-all) target=scripts/owner/stop-all.py;;
 reset-preview) target=scripts/owner/reset-preview.py;;
 preview-free) target=scripts/ops/preview-exec.sh;;
 *) fail 'unknown owner command.';;
esac
[ -f "$target" ] || fail "missing $target; pull main containing this step."
case "$command" in
 locks)
   [ "$apply" = 0 ] || fail 'locks is read-only; --apply is not supported.'
   exec .venv/bin/python "$target" "$PWD" --env-file "$envfile";;
 migrate)
   args=("$PWD" --env-file "$envfile")
   [ "$apply" = 0 ] || args+=(--apply --review-projection-functions)
   exec .venv/bin/python "$target" "${args[@]}";;
 policy)
   export REQUI_POLICY_DRY_RUN=$((1-apply))
   exec bash "$target";;
 preflight)
   [ "$apply" = 0 ] || fail 'preflight is read-only; --apply is not supported.'
   if [ -z "$email" ]; then printf 'Exact signed-up owner email: '; read -r email; fi
   [ -n "$email" ] || fail 'signed-up email required.'
   exec .venv/bin/python "$target" "$PWD" --env-file "$envfile" --email "$email";;
 force-stop) fail 'force-stop retired; use req reset-preview for a fresh dry owner reset review.';;
 stop-all)
   args=("$PWD" --env-file "$envfile")
   [ "$apply" = 1 ] || args+=(--dry-run)
   exec .venv/bin/python "$target" "${args[@]}";;
 reset-preview)
   args=("$PWD" --env-file "$envfile" --owner 9ed32462-3dd0-4222-90b3-92cb38e00911 --accounting wipe)
   [ "$apply" = 0 ] || args+=(--apply)
   exec .venv/bin/python "$target" "${args[@]}";;
 preview-free)
   if [ "$apply" = 0 ]; then
     echo 'DRY REVIEW ONLY: free Ollama phi4 preview, Docker/Mac, saved LIVE DB settings. No paid route. No container/model/DB access by this dry review.'
     echo 'To build/start with the existing typed confirmation: req preview-free --apply'
     exit 0
   fi
   export PREVIEW_EXEC_PROVIDER=ollama PREVIEW_EXEC_OLLAMA_MODEL=phi4
   unset PREVIEW_PAID_CHILD_APPROVED
   exec bash "$target";;
esac
