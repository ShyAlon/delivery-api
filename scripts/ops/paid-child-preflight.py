#!/usr/bin/env python3
"""Owner-run database evidence only. No activation, repairs, task or provider call."""
import argparse,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from delivery_api.paid_child_preflight import inspect
from delivery_api.attempt_ledger import AdmissionDenied


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('repo',type=Path);parser.add_argument('--env-file',type=Path);parser.add_argument('--email',required=True);args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('ops_settings',ROOT/'scripts/ops/settings.py');settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
    values=settings.load(args.repo,explicit=args.env_file)
    dsn=values.get('REQUI_ADMIN_DATABASE_URL')
    if not dsn:raise SystemExit('Inspecting database connection is not configured; nothing changed.')
    import psycopg
    try:
        with psycopg.connect(dsn,connect_timeout=10,options='-c default_transaction_read_only=on') as conn:
            result=inspect(conn,args.email)
            print(json.dumps(result,indent=2))
            if not result['database_policy_ready']:raise SystemExit('Confirmed-cost guard exhausted; nothing started.')
    except AdmissionDenied as exc:raise SystemExit(str(exc)) from None
    except Exception:raise SystemExit('Database inspection failed; private driver details excluded. Nothing changed.') from None


if __name__=='__main__':main()
