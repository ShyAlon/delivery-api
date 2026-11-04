#!/usr/bin/env bash
# Invoked by the existing req runner AFTER clean-main synchronization.
set -euo pipefail
. "$(dirname "$0")/terminal-log.sh"
cd "$(dirname "$0")/../.."
fail(){ printf 'REQ ABORTED: %s No owner step was run.\n' "$*" >&2; exit 1; }
command=${1:-}; shift
# Acceptance has its own exact argparse contract; pass every argument unchanged
# to the one repo entry rather than teaching this generic parser paid flags.
if [ "$command" = image ]; then exec bash scripts/ops/image.sh "$@"; fi
if [ "$command" = acceptance ]; then exec bash scripts/ops/acceptance.sh "$@"; fi
apply=0; email=''; checks=full; acceptance_args=(); test_owner=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --test-owner) [ "$command" = backoffice-access ] || fail "--test-owner is only for backoffice-access.";test_owner=1;shift;;
    --apply) apply=1;shift;;
    --fast) checks=fast;shift;;
    --full-checks) checks=full;shift;;
    --base-url) [ "$command" = preflight ] && [ "$#" -ge 2 ] || fail "$1 is only for preflight and needs a value.";acceptance_args+=("$1" "$2");shift 2;;
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
 backoffice-access) target=scripts/owner/backoffice-access.py;;
 debug) target=scripts/ops/preview-debug.py;;
 locks) target=scripts/owner/locks.py;;
 migrate) target=scripts/owner/apply-pg-cutover.py;;
 policy) target=scripts/ops/confirmed-expense-policy.sh;;
 preflight) target=scripts/ops/paid-child-preflight.py;;
 force-stop) target=scripts/owner/force-stop-all.py;;
 stop-all) target=scripts/owner/stop-all.py;;
 reset-preview) target=scripts/owner/reset-preview.py;;
 preview|preview-free) target=scripts/ops/preview-exec.sh;;
 *) fail 'unknown owner command.';;
esac
[ -f "$target" ] || fail "missing $target; pull main containing this step."
case "$command" in
 backoffice-access)
   args=("$PWD" --env-file "$envfile")
   [ "$test_owner" = 0 ] || args+=(--test-owner)
   [ "$apply" = 0 ] || args+=(--apply)
   exec .venv/bin/python "$target" "${args[@]}";;
 debug) [ "$apply" = 0 ] || fail "debug is read-only";exec .venv/bin/python "$target";;
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
   if [ -z "$email" ]; then ops_read email 'Exact signed-up owner email:'; fi
   [ -n "$email" ] || fail 'signed-up email required.'
   # Existing owner policy proof and acceptance checks are one command. Neither
   # result grants activation or edits a missing policy.
   .venv/bin/python "$target" "$PWD" --email "$email" || exit $?
   exec bash scripts/ops/acceptance.sh --dry-run "${acceptance_args[@]}";;
 force-stop) fail 'force-stop retired; use req reset-preview for a fresh dry owner reset review.';;
 stop-all)
   args=("$PWD" --env-file "$envfile")
   [ "$apply" = 1 ] || args+=(--dry-run)
   exec .venv/bin/python "$target" "${args[@]}";;
 reset-preview)
   args=("$PWD" --env-file "$envfile" --owner 9ed32462-3dd0-4222-90b3-92cb38e00911 --accounting wipe)
   [ "$apply" = 0 ] || args+=(--apply)
   exec .venv/bin/python "$target" "${args[@]}";;
 preview|preview-free)
   [ "$command" != preview-free ] || echo 'preview-free is an alias of the same accepted-image runtime, not an execution-off mode.'
   args=()
   [ "$apply" = 0 ] || args+=(--apply)
   exec bash "$target" "${args[@]}";;
esac
