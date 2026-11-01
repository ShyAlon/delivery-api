#!/usr/bin/env python3
"""Owner-run stop-only batch. No connection is made before explicit preflight passes."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID


def fail(message):
    raise SystemExit('RESET ABORTED: '+message)


def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    if result.returncode:
        fail('git '+args[0]+' failed. Fix Git access/check out current main, then retry. No reset was run.')
    return result.stdout.strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run',action='store_true',help='exit after review, before any Stop confirmation')
    parser.add_argument('repo',help='path to delivery-api checkout')
    parser.add_argument('--container',default='requi-preview-exec',help='running local preview container')
    parser.add_argument('--env-file',help='explicit settings file; default existing preview settings, otherwise checkout .env')
    parser.add_argument('--owner',help='authenticated Supabase owner UUID (or RESET_OWNER_ID)')
    parser.add_argument('--auth-session',help='active auth session UUID (or RESET_AUTH_SESSION_ID)')
    args=parser.parse_args()
    try:
        root=Path(args.repo).expanduser().resolve()
    except (OSError,RuntimeError) as exc:
        fail('cannot resolve repo path '+repr(args.repo)+' ('+type(exc).__name__+'). Pass the checkout directory, not a file.')
    markers=('scripts/ops-reset-existing-tasks.py','src/delivery_api/postgres_store.py','pyproject.toml')
    missing_markers=[name for name in markers if not (root/name).is_file()]
    script=root/markers[0]
    if not root.is_dir() or missing_markers:
        print('Input repo path:',repr(args.repo),file=sys.stderr)
        print('Resolved repo path:',root,file=sys.stderr)
        print('Directory exists:',root.is_dir(),file=sys.stderr)
        for name in missing_markers:print('Missing required file:',root/name,file=sys.stderr)
        fail('checkout preflight failed. Check the path. If this is an older checkout, update it to current delivery-api main; this wrapper does not update or modify checkout files. No database connection was made.')
    python=root/'.venv/bin/python'
    if not python.is_file():
        fail('repo .venv/bin/python is missing. Install the current main dependencies first.')
    if Path(sys.prefix).resolve()!=(root/'.venv').resolve():
        os.execv(str(python),[str(python),str(Path(__file__).resolve()),*sys.argv[1:]])
    if sys.version_info<(3,11):fail('Python 3.11 or newer is required.')
    required=('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY')
    import importlib.util
    settings_path=root/'scripts/ops/settings.py'
    if not settings_path.is_file():fail('shared settings loader missing; update main first.')
    spec=importlib.util.spec_from_file_location('requi_owner_settings',settings_path)
    settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
    try:
        values=settings.load(root,explicit=args.env_file)
        before=dict(values)
        settings.require(values,required)
        for name in required:os.environ[name]=values[name]
    except settings.SettingsError as exc:fail(str(exc))
    sys.path.insert(0,str(root/'src'))
    from delivery_api.runtime_environment import neutralize_retired_storage
    neutralize_retired_storage()
    if not os.environ['DELIVERY_DATABASE_URL'].startswith(('postgresql://','postgres://')):
        fail('DELIVERY_DATABASE_URL must be an explicit PostgreSQL URL.')
    if not os.environ['DELIVERY_SUPABASE_URL'].startswith('https://'):
        fail('DELIVERY_SUPABASE_URL must use HTTPS.')
    # Empty explicit values also win over config.load_dotenv(override=False).
    print('Checking current main. This fetch changes local Git refs only, not your database.',flush=True)
    git(root,'fetch','origin','main')
    head=git(root,'rev-parse','HEAD');remote=git(root,'rev-parse','refs/remotes/origin/main')
    if head!=remote:fail('checkout HEAD is not current origin/main. Update/checkout main yourself, then rerun. Nothing was reset.')
    if git(root,'status','--porcelain','--untracked-files=no'):
        fail('tracked files have local changes. Use a clean current-main checkout.')
    def identity(value,label):
        if not value:
            value=settings.hidden_prompt(label+' UUID').strip()
        try:return str(UUID(value))
        except (ValueError,TypeError):fail(label+' must be a UUID, not an email, password, JWT or project ID.')
    if not sys.stdin.isatty():fail('run interactively in a terminal; confirmation cannot be piped.')
    if not (args.owner or values.get('RESET_OWNER_ID')) or not (args.auth_session or values.get('RESET_AUTH_SESSION_ID')):
        print('Owner ID: Supabase > Authentication > Users > your signed-in user > User UID. Set RESET_OWNER_ID in the same settings file if desired.')
        print('Auth-session ID: sign in to Requi.re first. In Supabase SQL Editor run this READ-ONLY query, replacing OWNER_UUID with that UID:')
        print("SELECT id AS auth_session_id, user_id AS owner_id, created_at, not_after FROM auth.sessions WHERE user_id = 'OWNER_UUID'::uuid AND (not_after IS NULL OR not_after > now()) ORDER BY created_at DESC;")
        print('Use the session corresponding to your current sign-in. Set RESET_AUTH_SESSION_ID in the same file if desired. The script verifies the owner/session is active. Do not copy a JWT, access token or password.')
    owner=identity(args.owner or values.get('RESET_OWNER_ID'),'Owner')
    session=identity(args.auth_session or values.get('RESET_AUTH_SESSION_ID'),'Active auth session')
    os.chdir(root);sys.path.insert(0,str(root/'src'))
    try:
        import importlib.util
        spec=importlib.util.spec_from_file_location('owner_task_reset',script)
        reset=importlib.util.module_from_spec(spec);spec.loader.exec_module(reset)
        from delivery_api.postgres_store import PostgresStore
        from delivery_api.tenancy import TenantScope,tenant_context
        store=PostgresStore(os.environ['DELIVERY_DATABASE_URL'])
    except Exception as exc:
        fail('configuration/dependency preflight failed ('+type(exc).__name__+'). Install current dependencies and check trusted config; no reset was run.')
    print('Repository:',root,'\nMain commit:',head,'\nOwner:',owner)
    print('Stop-only owner batch. Preview must already be running. This script cannot kill workers, release holds or recover old checkpoints.')
    with tenant_context(TenantScope(owner,session)):
        if not store.auth_session_active():fail('owner/auth session is not active; no reset was run.')
        values['RESET_OWNER_ID']=owner;values['RESET_AUTH_SESSION_ID']=session
        if values!=before and not args.dry_run:
            try:settings.save_confirmed(Path(args.env_file).expanduser() if args.env_file else settings.target_path(),{k:v for k,v in values.items() if before.get(k)!=v})
            except settings.SettingsError as exc:fail(str(exc))
        stop_path=root/'scripts/ops-stop-existing-tasks.py'
        if not stop_path.is_file():fail('Stop All script missing; merge/update its PR before running. No Stop requested.')
        spec=importlib.util.spec_from_file_location('owner_stop_batch',stop_path)
        batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)
        reviewed=batch.plan(store)
        print('\n=== STOP ALL DRY REVIEW: NO CHANGES ===')
        import json
        for item in reviewed['tasks']:
            print(json.dumps({'task_id':item['task_id'],'title':item['title'],'state':item['state'],
                'generation':item['generation'],'session_bound':bool(item['session']),
                'worker_states':[j['state'] for j in item['work']['worker_jobs']],
                'provider_states':[a['state'] for a in item['work']['provider_attempts']],
                'quiescence':'unverified until engine confirms Stop','inventory_capped':item['inventory_capped']},sort_keys=True))
        print('Stop candidates:',len(reviewed['tasks']),'PLAN FINGERPRINT:',reviewed['fingerprint'])
        print('This only requests cooperative Stop. No Delete, retry/start, worker-state rewrite, release/refund/credit change, or invented quiescence. Accepted earlier calls may still settle. Missing legacy sessions remain blocked.')
        if args.dry_run:print('DRY RUN ONLY. No Stop requested.');return
        if not reviewed['tasks']:print('No blocked tasks need Stop. No changes.');return
        if input('Type CONTINUE to verify the running local preview, or Enter to exit dry-only: ').strip()!='CONTINUE':
            print('DRY RUN ONLY. No changes.');return
        try:engine=batch.PreviewEngine(args.container,owner,session,os.environ['DELIVERY_DATABASE_URL'])
        except Exception as exc:fail('preview preflight failed ('+type(exc).__name__+'). Start the current preview against the same settings file. No Stop requested.')
        results=batch.apply(store,engine,reviewed)
        print('\n=== STOP ALL RESULTS ===')
        for result in results:print(json.dumps(result,sort_keys=True))
        print('No task deleted. Run reset dry-run again. Any pending/blocked task must remain blocked until verified worker exit and provider reconciliation.')


if __name__=='__main__':
    try:main()
    except (EOFError,KeyboardInterrupt):raise SystemExit('STOP ALL CANCELLED: earlier Stop requests, if any, persist. Run dry review again; nothing was deleted.')
