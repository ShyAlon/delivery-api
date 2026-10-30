#!/usr/bin/env bash
# Owner-run, explicit modern-code confirmation. No automatic activation or grants.
set -euo pipefail
OPS_NOTE="No transition was committed."
. "$(dirname "$0")/lib.sh"; ops_start own-test-profile "$0" --venv
ops_settings REQUI_ADMIN_DATABASE_URL
export REQUI_ADMIN_DATABASE_URL
.venv/bin/python - <<'PY'
import os,json
import psycopg
from delivery_api.own_test_transition import transition
print('This replaces ONLY your legacy $2 reservation ceiling with the separate $2 confirmed-expense rolling24h tripwire. It does not enable launch guards, paid children, or public execution. Stop and reconcile your work first. Apply migrations and restart the reviewed code BEFORE running this transition.')
with open('/dev/tty') as tty:
    print('Own signed-up email: ',end='',flush=True);email=tty.readline().strip()
with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
    print(json.dumps(transition(conn,email),indent=2));conn.rollback()
print('Type exactly: reviewed runtime includes own-test dispatch guard')
with open('/dev/tty') as tty:ack=tty.readline().strip()
if ack!='reviewed runtime includes own-test dispatch guard':raise SystemExit('Not confirmed; nothing written.')
print('Type yes to revalidate and commit this exact transition: ',end='',flush=True)
with open('/dev/tty') as tty:ack=tty.readline().strip()
if ack!='yes':raise SystemExit('Not confirmed; nothing written.')
with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
    print(json.dumps(transition(conn,email,apply=True),indent=2))
print('Committed. No execution started. Keep this output as transition evidence.')
PY
