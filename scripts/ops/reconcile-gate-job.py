#!/usr/bin/env python3
"""Dry-run-first owner repair. Core predicates are shared with the gate writer."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from delivery_api.gate_job_reconciliation import reconcile

def main(argv=None):
    """Standalone, file-first, dry-run default. Administrative URL is explicit."""
    import argparse
    import sys
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repo',type=Path);p.add_argument('--task',required=True);p.add_argument('--phase',default='architecture')
    p.add_argument('--job',required=True);p.add_argument('--apply',metavar='DRY_RUN_FINGERPRINT')
    args=p.parse_args(argv);root=args.repo.expanduser().resolve()
    if not (root/'pyproject.toml').is_file():p.error('repo must be the delivery-api checkout')
    sys.path.insert(0,str(root/'scripts/ops'))
    try:
        import settings
        import psycopg
        from psycopg.rows import dict_row
        values=settings.load(root)
        dsn=values.get('REQUI_ADMIN_DATABASE_URL')
        if not dsn:raise ValueError('REQUI_ADMIN_DATABASE_URL is not saved in the private settings; no connection or changes made. Do not paste a database URL into chat.')
        with psycopg.connect(dsn,row_factory=dict_row,connect_timeout=10) as conn:
            conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='15s'")
            plan=reconcile(conn.execute,args.task,args.phase,apply=bool(args.apply),expected=args.apply,only_job=args.job,actor='owner-terminal-repair')
            if not args.apply:conn.rollback()
        print(json.dumps(plan,indent=2,sort_keys=True))
        if not args.apply:print('DRY RUN ONLY. No rows changed. Review this plan before rerunning with --apply and its exact fingerprint.')
    except Exception as exc:
        # Driver errors can contain credentials/DSNs or server detail. Never print them.
        print('STOP: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'; database check failed, no repair committed. Check the local database connection and permissions.'),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
