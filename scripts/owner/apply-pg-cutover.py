#!/usr/bin/env python3
"""Owner-run additive PG cutover review. Dry by default; no deploy or model call.

Refuses pending DROP/DELETE/TRUNCATE except existing exact retention handling
and opt-in, separately typed review of exact private projection-function files.
Existing objects are NOT proof a migration's full SQL ran. Catalog and optional
Supabase ledger are shown separately. Partial files stop the workflow.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


# Owner yes2026-10-30 16:45:41 IST, exact reviewed metrics-only function.
# Exact index-only replacement still requires the owner APPLY confirmation.
APPROVED_DESTRUCTIVE={'20261101124000_visible_task_names.sql':'c39397fa2d8718d6c290b0807b1c56bba21d6275ad560f0b6e320ccdfa292189','20261030030000_resource_metrics_maintenance.sql':'8bef488bdc0d6795624821d2cd06c6fb663276ccc27643bd2311ab0ec56cb07a'}

# Exact additive migrations: DELETE occurs in private derived-cache functions,
# not a grant to delete provider attempts, artifacts, task history or money rows.
# Hash mismatch, rename or another destructive keyword always refuses.
PROJECTION_FUNCTION_REVIEW={
 '20261102070000_uncertainty_is_audit_not_admission.sql':'ddb46f7c130b92aec1be95de754580559840ac3c38de34f153e8673fc67597c7',
 '20261031163000_admission_totals.sql':'3e62b46bb60da59c02016830f5fd74492d10b435714c253f56a7dd2a9c229b10',
 '20261031180000_expense_totals.sql':'06a9b6f68613018c2e2f265f9d2ab7d4c6ccab7ee0a8fda1e37a016458a0e44b',
}

# Exact schema-only constraint widening, no data deletion. Always separately reviewed by owner.
MODEL_PIN_SCHEMA_REVIEW={'20261101160000_model_pins.sql':'968d8b882b4611ce4cee375ca9880f5398944c61ba42a07cd67077b4174ee8c0'}

def review_projection_functions(rows,confirm):
    allowed={}
    for row in rows:
        if row['status']!='pending' or not row['destructive']:continue
        name=row['file'];digest=row.get('sha256')
        if APPROVED_DESTRUCTIVE.get(name)==digest:continue
        if MODEL_PIN_SCHEMA_REVIEW.get(name)==digest and row['destructive']==['DROP']:
            expected='REVIEW MODEL PIN SCHEMA '+name+' SHA256 '+digest
            print('Widens the operator capability constraint; no source data is deleted. Read the exact SQL.')
            if confirm('Type "'+expected+'": ')!=expected:raise ValueError('model pin schema review did not match; no changes')
            allowed[name]=digest
            continue
        if PROJECTION_FUNCTION_REVIEW.get(name)!=digest or row['destructive']!=['DELETE']:
            raise ValueError('refused destructive migration '+name+'; not an exact reviewed projection-function file')
        print('REVIEW',name,'SHA256',digest)
        print('Defines private projection functions or clears derived caches only. Does not delete source task, artifact, money or provider evidence.')
        print('Read the exact SQL before confirming. Creating these functions enables their later normal trigger/read behavior.')
        expected='REVIEW FUNCTIONS '+name+' SHA256 '+digest
        if confirm('Type "'+expected+'": ')!=expected:raise ValueError('projection-function confirmation did not match; no changes')
        allowed[name]=digest
    return allowed

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def plan(conn,readiness,root):
    rows=readiness.inventory(conn)
    ledger=None
    if conn.execute("SELECT to_regclass('supabase_migrations.schema_migrations') IS NOT NULL").fetchone()[0]:
        ledger={str(x[0]) for x in conn.execute('SELECT version FROM supabase_migrations.schema_migrations').fetchall()}
    for row in rows:
        path=root/'supabase/migrations'/row['file'];row['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        row['ledger']='unavailable' if ledger is None else 'recorded' if row['file'].split('_')[0] in ledger else 'not-recorded'
        if row['ledger']=='recorded' and row['status']!='present':raise ValueError('ledger/catalog disagree for '+row['file']+'; no automatic repair')
    return rows

def apply(conn,reviewed,readiness,root,app_login,confirm,*,review_functions=False):
    if any(r['status']=='partial' for r in reviewed):raise ValueError('partial migration objects; manual review required, no automatic replay')
    pending=[r for r in reviewed if r['status']=='pending']
    print('Type only the text inside quotes, without the quote marks. Press Enter to cancel.')
    reviewed_functions=review_projection_functions(reviewed,confirm) if review_functions else {}
    allowed={**APPROVED_DESTRUCTIVE,**reviewed_functions}
    blocked=[r['file']+': '+','.join(r['destructive']) for r in pending if r['destructive'] and (r['file'] not in allowed or allowed[r['file']]!=r.get('sha256'))]
    if blocked:raise ValueError('refused pending destructive migration(s): '+'; '.join(blocked)+'; no migrations or grants applied')
    if confirm('Type "APPLY '+str(len(pending))+' MIGRATIONS TO '+app_login+'" to apply in order and grant restricted SET memberships: ')!='APPLY '+str(len(pending))+' MIGRATIONS TO '+app_login:
        raise ValueError('confirmation did not match; no changes')
    if plan(conn,readiness,root)!=reviewed:raise ValueError('review changed; rerun dry review')
    import psycopg
    print('ROLLBACK: each file is atomic. Earlier APPLIED files persist after a later failure; rerun dry review to resume.')
    print('No automatic down-migration: do not drop projection tables or revert runtimes against mismatched schema. Review a forward repair or restore a verified backup.')
    for row in reviewed:
        print('PLAN',row['status'].upper(),row['file'],'SHA256',row.get('sha256','unavailable'))
    for row in pending:
        print('APPLYING',row['file'],'SHA256',row.get('sha256','unavailable'))
        try:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='60s'")
                conn.execute((root/'supabase/migrations'/row['file']).read_text())
            print('APPLIED',row['file'])
        except psycopg.Error as exc:
            print('FAILED',row['file'],'SQLSTATE',exc.sqlstate,'ERROR',exc.diag.message_primary or type(exc).__name__,file=sys.stderr)
            print('This file rolled back. Earlier APPLIED files persist. No automatic retry or later file executed.',file=sys.stderr)
            return 1
    from psycopg import sql
    # Separate atomic role-grant transaction, only fixed restricted roles and the
    # role parsed from the owner's explicit app DSN. No broad database/table grants.
    with conn.transaction():
        for role in readiness.ROLES:
            conn.execute(sql.SQL('GRANT {} TO {} WITH INHERIT FALSE, SET TRUE').format(sql.Identifier(role),sql.Identifier(app_login)))
    print('Restricted SET memberships granted to',app_login)
    print('No Supabase migration ledger was fabricated or updated. Rerun dry inventory and preview schema check.');return 0

def identity(conn):
    return tuple(conn.execute('SELECT session_user,current_user').fetchone())

def check_admin(conn):
    create_role,create_schema=conn.execute("SELECT (rolsuper OR rolcreaterole),has_schema_privilege(current_user,'public','CREATE') FROM pg_roles WHERE rolname=current_user").fetchone()
    if not create_role or not create_schema:
        raise ValueError('admin connection lacks CREATE ROLE or CREATE on public; use a DDL-capable admin URL, not the restricted app URL. No migrations or grants applied')

def report_sources(s,root,explicit):
    for name in ('DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL'):
        print(s.source_report(root,name,explicit=explicit))


def parse_database_url(value,label):
    # Validate RAW authority before libpq/url parsing. A raw password delimiter
    # can otherwise be reclassified as hostname/path and leak in diagnostics.
    import re
    from urllib.parse import urlsplit,parse_qsl
    from psycopg.conninfo import conninfo_to_dict
    message=label+' database URL has invalid userinfo. Password special characters must be URL-encoded (@ -> %40, / -> %2F, ? -> %3F, # -> %23, % -> %25). Encode only the password locally, not the whole URL. No URL fragments printed'
    try:
        if not value.startswith(('postgresql://','postgres://')):raise ValueError()
        parsed=urlsplit(value)
        if parsed.fragment or parsed.netloc.count('@')!=1:raise ValueError()
        userinfo,hostpart=parsed.netloc.rsplit('@',1)
        if ':' not in userinfo:raise ValueError()
        username,password=userinfo.split(':',1)
        if not username or not password:raise ValueError()
        # Only RFC unreserved bytes or complete percent escapes are accepted in
        # userinfo. This deliberately rejects ambiguous raw reserved characters.
        safe=r'(?:[A-Za-z0-9_.~-]|%[0-9A-Fa-f]{2})+'
        if not re.fullmatch(safe,username) or not re.fullmatch(safe,password):raise ValueError()
        if not parsed.hostname or not re.fullmatch(r'[A-Za-z0-9.-]+',parsed.hostname):raise ValueError()
        if not re.fullmatch(r'/[A-Za-z0-9_.~-]+',parsed.path):raise ValueError()
        if any(key.lower() in ('host','hostaddr','port','dbname','user','password') for key,_ in parse_qsl(parsed.query)):raise ValueError()
        result=conninfo_to_dict(value)
        if '@' in result.get('host','') or any(c in result.get('host','') for c in '/?#'):raise ValueError()
        return result
    except Exception:raise ValueError(message) from None


def check_targets(admin,app):
    differences=[]
    for key,label,default in (('host','host',''),('port','port','5432'),('dbname','database','')):
        left=admin.get(key,default);right=app.get(key,default)
        if left!=right:
            # Only non-secret endpoint components, never URL/user/password/options.
            clean=lambda value:str(value).replace('\n','?').replace('\r','?')
            differences.append(label+': admin='+clean(left)+'; app='+clean(right))
    if differences:
        raise ValueError('admin/app targets differ: '+' | '.join(differences)+'. Keep the app URL unchanged. Use its same session-pooler host, port and database for the admin URL; direct db.<ref>.supabase.co or transaction-pooler port6543 may be the wrong connection mode. Login names may differ and are not compared. No database changes')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('repo');p.add_argument('--apply',action='store_true');p.add_argument('--preview-offer',action='store_true',help='return to preview after apply; do not run separate operator file setup');p.add_argument('--env-file');p.add_argument('--review-projection-functions',action='store_true',help='allow a separate exact filename+SHA typed review of known private cache DELETE function definitions; not arbitrary destructive SQL');p.add_argument('--identify',action='store_true',help='read-only app identity and settings source only; no admin URL required');args=p.parse_args()
    root=Path(args.repo).expanduser().resolve();py=root/'.venv/bin/python'
    if not py.is_file():raise ValueError('checkout .venv missing')
    if Path(sys.prefix).resolve()!=(root/'.venv').resolve():os.execv(str(py),[str(py),str(Path(__file__).resolve()),*sys.argv[1:]])
    s=module(root/'scripts/ops/settings.py','requi_migration_settings');r=module(root/'scripts/ops/schema-readiness.py','requi_migration_readiness')
    values=s.load(root,explicit=args.env_file)
    report_sources(s,root,args.env_file)
    s.require(values,['DELIVERY_DATABASE_URL'])
    if args.identify:
        parse_database_url(values['DELIVERY_DATABASE_URL'],'app')
        import psycopg
        with psycopg.connect(values['DELIVERY_DATABASE_URL'],connect_timeout=10,autocommit=True) as conn:
            conn.execute('SET default_transaction_read_only=on')
            login,role=identity(conn)
        print('APP identity before any SET ROLE: session_user='+login+' current_user='+role)
        print('READ ONLY. No admin URL reused, no settings saved, no migration or grant applied.');return 0
    s.require(values,['REQUI_ADMIN_DATABASE_URL'])
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    admin=parse_database_url(values['REQUI_ADMIN_DATABASE_URL'],'admin');app=parse_database_url(values['DELIVERY_DATABASE_URL'],'app')
    # Prove the actual app role through its own connection, including pooler
    # usernames such as role.project. Never parse/guess the grant recipient.
    check_targets(admin,app)
    with psycopg.connect(values['DELIVERY_DATABASE_URL'],connect_timeout=10,autocommit=True) as app_conn:
        app_conn.execute('SET default_transaction_read_only=on')
        app_session,app_login=identity(app_conn)
        print('APP identity before SET ROLE: session_user='+app_session+' current_user='+app_login)
    if app_login in ('postgres','supabase_admin'):raise ValueError('app connection uses an admin role; refusing broad grants')
    with psycopg.connect(values['REQUI_ADMIN_DATABASE_URL'],connect_timeout=10,autocommit=True) as conn:
        # No writes occur until apply() passes complete inventory, safety scan and typed confirmation.
        conn.execute('SET default_transaction_read_only=on')
        admin_session,admin_role=identity(conn)
        print('ADMIN identity: session_user='+admin_session+' current_user='+admin_role)
        check_admin(conn)
        if not all(r.object_exists(conn,'table:'+name) for name in ('tasks','provider_attempts','worker_jobs','retail_contributions')):
            raise ValueError('pre-cutover baseline schema missing; refuse automatic baseline replay. No changes')
        reviewed=plan(conn,r,root)
        print('PREFLIGHT PASSED: protected settings, matching admin/app target, app identity, DDL capability and complete migration inventory.')
        print('DRY REVIEW, NO DATABASE CHANGES; app login:',app_login)
        print('ROLLBACK: each migration has its own transaction; a failed file rolls back, earlier APPLIED files persist. Rerun dry review to resume. No automatic down-migration or runtime rollback.')
        for row in reviewed:print(json.dumps(row,sort_keys=True))
        print('Catalog presence does not prove exact applied SQL/checksum. Supabase ledger shown only if available. No older migration is replayed automatically.')
        if not args.apply:print('DRY ONLY. To request changes rerun with --apply; partial or unknown destructive migrations still refuse. Exact private cache functions require --review-projection-functions and their separate typed confirmations.');return 0
        if not sys.stdin.isatty():raise ValueError('apply requires interactive terminal')
        conn.execute('SET default_transaction_read_only=off')
        result=apply(conn,reviewed,r,root,app_login,s.terminal_confirm,review_functions=args.review_projection_functions)
    if result:return result
    if args.preview_offer:return 0
    # Standard operator configuration, not a past-task repair. The existing
    # setup owns its own explicit private-file confirmation after DB apply.
    operator_setup=module(root/'scripts/ops/operator-config-setup.py','requi_operator_setup')
    return operator_setup.run(root,explicit=args.env_file)

if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,EOFError,KeyboardInterrupt) as exc:raise SystemExit('MIGRATION ABORTED: '+str(exc))
    except Exception as exc:raise SystemExit('MIGRATION ABORTED: '+type(exc).__name__+'; no credential-bearing connection error printed. Earlier APPLIED files, if any, persist.')
