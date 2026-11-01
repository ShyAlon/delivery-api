#!/usr/bin/env python3
"""Owner-only forced administrative job closure. Dry-run default; no process kill."""
import argparse,importlib.util,json,sys
from pathlib import Path
from uuid import UUID


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repo',type=Path);p.add_argument('--env-file',type=Path)
    p.add_argument('--owner');p.add_argument('--auth-session');p.add_argument('--apply',action='store_true')
    args=p.parse_args();root=args.repo.expanduser().resolve();sys.path.insert(0,str(root/'src'))
    spec=importlib.util.spec_from_file_location('settings',root/'scripts/ops/settings.py')
    settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
    values=settings.load(root,explicit=args.env_file)
    owner=str(UUID(args.owner or values.get('RESET_OWNER_ID') or input('Signed-in owner UUID: ').strip()))
    session=str(UUID(args.auth_session or values.get('RESET_AUTH_SESSION_ID') or input('Active auth-session UUID: ').strip()))
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope,tenant_context
    from delivery_api.attempt_ledger import AdmissionDenied
    store=PostgresStore(values['DELIVERY_DATABASE_URL'])
    with tenant_context(TenantScope(owner,session)):
        review=store.force_stop_jobs_plan()
        print(json.dumps(review,indent=2,default=str))
        if not args.apply:
            print('DRY RUN ONLY. No changes. Rerun with --apply to review a fresh plan and enter its exact confirmation.');return
        if not sys.stdin.isatty():raise SystemExit('Interactive terminal required; no piped confirmations.')
        phrase=review['confirmation']
        answer=input('Type '+phrase+' to accept the UNVERIFIED worker-exit override: ')
        print(json.dumps(store.force_stop_jobs_apply(review['fingerprint'],answer),indent=2,default=str))


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,EOFError):raise SystemExit('Cancelled. Re-run dry review if a commit may have completed.') from None
    except Exception as exc:
        # Domain errors are safe; driver/transport details can contain secrets.
        from delivery_api.tenancy import TenantAccessDenied
        from delivery_api.attempt_ledger import AdmissionDenied
        if isinstance(exc,(ValueError,TenantAccessDenied,AdmissionDenied)):
            raise SystemExit('FORCE STOP REFUSED: '+str(exc)) from None
        raise SystemExit('FORCE STOP failed ('+type(exc).__name__+'); no automatic retry. Inspect a fresh dry review; private driver details hidden.') from None
