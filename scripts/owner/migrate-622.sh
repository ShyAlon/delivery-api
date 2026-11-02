#!/usr/bin/env bash
set -euo pipefail
# #622 owner-run schema update. No task/accounting reset, deploy or model call.
ROOT="${REQUI_ROOT:-$HOME/development/require}/delivery-api"
fail(){ printf 'STOP: %s\n' "$*" >&2; exit 1; }
[ -d "$ROOT/.git" ] || fail "delivery-api checkout missing: $ROOT"
cd "$ROOT"
[ "$(git branch --show-current)" = main ] || fail 'Switch delivery-api to main first.'
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail 'Tracked changes present. Commit or stash them first.'
git fetch origin main
git merge --ff-only origin/main
. scripts/ops/terminal-log.sh
fail(){ error "STOP: $*" >&2; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail 'Not on current main.'
git merge-base --is-ancestor f05fb8b508112d6bd56ce4b3ac5520de40d5853c HEAD || fail 'Main does not contain merged #622.'
FILE=supabase/migrations/20261102070000_uncertainty_is_audit_not_admission.sql
EXPECTED=ddb46f7c130b92aec1be95de754580559840ac3c38de34f153e8673fc67597c7
ACTUAL=$(shasum -a 256 "$FILE" | awk '{print $1}')
[ "$ACTUAL" = "$EXPECTED" ] || fail 'Migration hash changed. Stop for review.'
[ -x .venv/bin/python ] || fail 'Checkout .venv missing.'
ENVFILE="$HOME/.config/requi/preview.env"
warn 'Stop the running preview with Ctrl-C in its terminal before applying. This script does not stop other work.'
printf 'First: read-only migration inventory. No database changes in this step.\n'
.venv/bin/python scripts/owner/apply-pg-cutover.py "$PWD" --env-file "$ENVFILE" --preview-offer
printf '\nReview the SQL below. It changes uncertainty-only guards and clears only rebuildable admission caches.\n'
printf 'Provider attempts, charges, tasks, artifacts and audit evidence are retained.\n'
warn 'Risk: the final total may exceed a ceiling by the unknown bill.'
printf 'Expected migration SHA256: %s\n' "$EXPECTED"
cat "$FILE"
ops_read ANSWER 'Continue to the interactive apply review? Type yes, or Enter to stop:'
[ "$ANSWER" = yes ] || { printf 'Stopped after dry review. No apply requested.\n'; exit 0; }
printf '\nFor this file, the exact review line is:\nREVIEW FUNCTIONS %s SHA256 %s\n' "${FILE##*/}" "$EXPECTED"
printf 'The final APPLY prompt names the actual pending file count and verified app role. Type exactly what it prints, without quotes. Do not assume it is one file.\n'
.venv/bin/python scripts/owner/apply-pg-cutover.py "$PWD" --env-file "$ENVFILE" --apply --review-projection-functions --preview-offer
printf '\nRead-only inventory after apply:\n'
.venv/bin/python scripts/owner/apply-pg-cutover.py "$PWD" --env-file "$ENVFILE" --preview-offer
printf '\nMigration review finished. Refresh preview in its own terminal:\n  req preview --apply --full-checks\n'
printf 'This rebuilds/checks the current main image. Starting preview does not approve a paid interview. Do not press Start until the reviewed model pins and spending policy are ready.\n'
