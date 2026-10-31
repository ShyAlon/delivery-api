#!/usr/bin/env bash
# Owner-run explicit policy preview/commit. No activation, grants or task start.
set -euo pipefail
OPS_NOTE="No policy change was committed."
. "$(dirname "$0")/lib.sh"; ops_start confirmed-expense-policy "$0" --venv
ops_settings REQUI_ADMIN_DATABASE_URL
export REQUI_ADMIN_DATABASE_URL
.venv/bin/python - <<'PY'
import os,json
import psycopg
from delivery_api.confirmed_policy_install import plan
print('Install only the reviewed $3/session and $3 x registered users/rolling24h confirmed-expense policy. Existing conflicting policy is refused, never replaced. All work and accounting must be quiescent/reconciled. This does NOT enable paid execution or send alerts. Verify migrated schema and the reviewed runtime before continuing.')
with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
    print(json.dumps(plan(conn),indent=2));conn.rollback()
print('Type exactly: reviewed runtime includes production launch guard')
with open('/dev/tty') as tty:ack=tty.readline().strip()
if ack!='reviewed runtime includes production launch guard':raise SystemExit('Not confirmed; nothing written.')
print('Type yes to revalidate and commit this policy only: ',end='',flush=True)
with open('/dev/tty') as tty:ack=tty.readline().strip()
if ack!='yes':raise SystemExit('Not confirmed; nothing written.')
with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
    print(json.dumps(plan(conn,apply=True),indent=2))
print('Policy ready. No execution activated, credits granted, profile/cap changed or alert sent.')
PY
