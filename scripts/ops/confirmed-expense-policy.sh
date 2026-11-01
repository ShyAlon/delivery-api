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
from delivery_api.confirmed_policy_install import plan,lock_diagnostics
from delivery_api.attempt_ledger import AdmissionDenied

def main():
    print('Install only the reviewed $3/session and $3 x registered users/rolling24h confirmed-expense policy. Existing conflicting policy is refused, never replaced. All work and accounting must be quiescent/reconciled. This does NOT enable paid execution or send alerts. Verify migrated schema and the reviewed runtime before continuing.')
    with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
        print(json.dumps(plan(conn),indent=2));conn.rollback()
    if os.environ.get('REQUI_POLICY_DRY_RUN')=='1':
        print('DRY RUN ONLY. No policy changes. Use req policy --apply for the existing explicit confirmations.');return
    print('Type exactly: reviewed runtime includes production launch guard')
    with open('/dev/tty') as tty:ack=tty.readline().strip()
    if ack!='reviewed runtime includes production launch guard':raise SystemExit('Not confirmed; nothing written.')
    print('Type yes to revalidate and commit this policy only: ',end='',flush=True)
    with open('/dev/tty') as tty:ack=tty.readline().strip()
    if ack!='yes':raise SystemExit('Not confirmed; nothing written.')
    with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL']) as conn:
        print(json.dumps(plan(conn,apply=True),indent=2))
    print('Policy ready. No execution activated, credits granted, profile/cap changed or alert sent.')

try:
    main()
except (psycopg.errors.QueryCanceled,psycopg.errors.LockNotAvailable):
    print('POLICY TIMEOUT: no policy change committed. Current policy/advisory lock holders (not proof of the expired wait edge):')
    try:
        with psycopg.connect(os.environ['REQUI_ADMIN_DATABASE_URL'],options='-c default_transaction_read_only=on -c statement_timeout=3000') as diagnostic:
            print(json.dumps(lock_diagnostics(diagnostic),indent=2,default=str))
    except Exception:
        print('Lock inspection unavailable. No automatic cancel/terminate. Ask the owner of any other active maintenance terminal to finish or roll back.')
    raise SystemExit('Retry only after reviewing the lock holder; no automatic retry or termination.') from None
except AdmissionDenied as exc:
    raise SystemExit('POLICY BLOCKED: '+str(exc)+'\nNo policy change committed. Inspect blockers; use normal Stop/reconciliation, never rewrite accounting.') from None

PY
