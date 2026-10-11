#!/usr/bin/env python3
"""Restore a private working copy for exact reviewed accounting, never model work.

Accounting and paid continuation have separate exact reviewed entry flags.
Accounting never installs provider transport. Original archive and source artifacts must be read-only mounts.
"""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
from psycopg.conninfo import make_conninfo
from bootstrap_database import bootstrap
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope,tenant_context
from recovery_plan import accounting_preview
from recovery_accounting import reconcile,waive_unknown_compute


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('--dump',type=Path,required=True);p.add_argument('--identity',type=Path,required=True)
    p.add_argument('--attempt',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--expected-dump-sha256',required=True)
    p.add_argument('--approve-plan');p.add_argument('--source-verification',choices=['matched-historic-snapshot'])
    p.add_argument('--waiver-authorization');p.add_argument('--pending-review',type=Path)
    p.add_argument('--continue-reviewed-sha256');p.add_argument('--continuation-review',type=Path)
    p.add_argument('--source-run',type=Path);p.add_argument('--runtime',type=Path)
    a=p.parse_args()
    if a.output.exists() or a.output.is_symlink():raise ValueError('new private output directory required')
    if a.dump.is_symlink() or a.identity.is_symlink():raise ValueError('regular original evidence required')
    if hashlib.sha256(a.dump.read_bytes()).hexdigest()!=a.expected_dump_sha256:raise ValueError('original reviewed archive changed')
    identity=json.loads(a.identity.read_text())
    if identity.get('stage')!='identity' or identity.get('source_mode')!='mac_acceptance' or identity.get('signature_verified') is not True:
        raise ValueError('original verified isolated identity receipt required')
    if identity.get('run_id')!='4eda39c0684945ff849b582c60dd91f8' or identity.get('subject')!='045bafdf-e35b-4b4b-93f2-0cd981b52806' or identity.get('session_id')!='16caab89-5c55-4c36-af25-ba65105ed74f' or a.attempt!='a1c69b8a218c49d2829dff112d0a2688':
        raise ValueError('exact stopped-run original identity and attempt required')
    scope=TenantScope(identity['subject'],identity['session_id']);db='requi_item6_recovery_'+identity['run_id'][:12]
    if a.continue_reviewed_sha256 and a.approve_plan:raise ValueError('accounting and paid continuation are separate invocations')
    if a.continue_reviewed_sha256:
        if not a.continuation_review or not a.source_run or not a.runtime:raise ValueError('review+source artifacts+runtime required')
        reviewed=json.loads(a.continuation_review.read_text())
        from recovery_plan import digest
        required={'version','original_run_id','reconciled_dump_sha256','original_failed_job','task_id','source_tree_sha256','runtime_tree_sha256','maximum_expense_units','new_purchase','uninterrupted_acceptance'}
        if reviewed.get('version')==2:required=required|{'pending_review_sha256'}
        if set(reviewed)!=required or reviewed['version'] not in (1,2) or reviewed['new_purchase'] is not False or reviewed['uninterrupted_acceptance'] is not False:
            raise ValueError('exact continuation review fields required')
        if type(reviewed['maximum_expense_units']) is not int or reviewed['maximum_expense_units']<=0:raise ValueError('approved positive provider expense budget required')
        from recovery_manifest import tree_digest
        if tree_digest(a.source_run)!=reviewed['source_tree_sha256'] or tree_digest(a.runtime)!=reviewed['runtime_tree_sha256']:
            raise ValueError('source or runtime tree differs from paid review')
        if digest(reviewed)!=a.continue_reviewed_sha256:raise ValueError('exact paid continuation review required')
        if reviewed['original_run_id']!=identity['run_id'] or reviewed['reconciled_dump_sha256']!=hashlib.sha256(a.dump.read_bytes()).hexdigest():raise ValueError('continuation archive/run changed')
        expected_prior='edad945ddadfa63b5bd73d5546a5bf69' if reviewed['version']==1 else '9e04190ef41fac9f64e5f75b96e60b77'
        if reviewed['original_failed_job']!=expected_prior or reviewed['task_id']!='c11813e4a4b7':raise ValueError('exact stopped task/job review required')
        if reviewed['version']==2:
            if reviewed['maximum_expense_units']!=1000000000000:raise ValueError('original cumulative one-dollar cap required')
            if not a.pending_review or not a.waiver_authorization or reviewed['reconciled_dump_sha256']!='ea2b834221db3ffc96c7e4ace98a604243ad700e0ec30dffe25d8349bb895e35':raise ValueError('exact interrupted archive and waiver authority required')
            if digest(json.loads(a.pending_review.read_text()))!=reviewed['pending_review_sha256'] or reviewed['pending_review_sha256']!='51cb41b39274a514f1fba96dfce16972eab0026d6324443fb09c5462a46092e3':raise ValueError('exact pending choice required')
    if a.approve_plan and (not a.source_verification or not a.waiver_authorization):raise ValueError('exact accounting review, source evidence and waiver required')
    data=Path('/tmp/item6-recovery-pg')
    if data.exists():raise ValueError('new ephemeral recovery database required')
    a.output.mkdir(mode=0o700)
    subprocess.run(['/usr/lib/postgresql/15/bin/initdb','-D',str(data),'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'-l','/tmp/recovery-pg.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        subprocess.run(['/usr/lib/postgresql/15/bin/createdb','-h','127.0.0.1','-p','55439','--template=template0',db],check=True)
        admin=make_conninfo(host='127.0.0.1',port=55439,user='requi',dbname=db)
        bootstrap(admin,'/app/migrations')
        # Data only into the exact fresh migrated schema. No existing DB reset.
        subprocess.run(['/usr/lib/postgresql/15/bin/pg_restore','--exit-on-error','--data-only','--disable-triggers','--no-owner','--no-privileges','--dbname='+admin,str(a.dump)],check=True,stdout=subprocess.DEVNULL)
        store=PostgresStore(make_conninfo(host='127.0.0.1',port=55439,user='delivery_app',dbname=db))
        archive_hash=hashlib.sha256(a.dump.read_bytes()).hexdigest()
        with tenant_context(scope):
            if not store.auth_session_active():raise ValueError('original restored auth session inactive')
            with store._postgres.transaction() as c:
                row=c.execute('SELECT * FROM provider_attempts WHERE attempt_id=%s',(a.attempt,)).fetchone()
                if not row:raise ValueError('exact owned attempt absent')
                job=c.execute('SELECT * FROM worker_jobs WHERE job_id=%s',(row['job_id'],)).fetchone()
                account=c.execute('SELECT * FROM credit_accounts').fetchone()
            if a.continue_reviewed_sha256:
                import shutil
                from recovery_plan import digest
                # No source mutation: copy immutable original tree into output.
                for path in a.source_run.rglob('*'):
                    if path.is_symlink():raise ValueError('original source symlink forbidden')
                from recovery_checkpoint import validate_checkpoint
                if a.output.resolve().is_relative_to(a.source_run.resolve()):raise ValueError('recovery output cannot be inside original source')
                root=a.output/'working-run'
                shutil.copytree(a.source_run,root)
                if tree_digest(root)!=reviewed['source_tree_sha256']:raise ValueError('copied source tree changed')
                from recovery_run import run
                try:
                    if reviewed['version']==2:
                        from recovery_interrupted import validate_interrupted_ledger
                        validate_interrupted_ledger(store)
                        # Validate pending input BEFORE even the approved compute waiver.
                        from recovery_interrupted import validate_pending_files
                        validate_pending_files(root,a.pending_review)
                        waive_unknown_compute(store,reviewed['original_failed_job']+':compute',original_dump_sha256=archive_hash,authorization_ref=a.waiver_authorization)
                    result=run(store=store,scope=scope,root=root,runtime=a.runtime,run_id=identity['run_id'],task_id=job['task_id'],prior_job=reviewed['original_failed_job'],interrupted_job=reviewed['original_failed_job'] if reviewed['version']==2 else None,pending_review=json.loads(a.pending_review.read_text()) if reviewed['version']==2 else None,
                       output=a.output/'continuation',maximum_expense_units=reviewed['maximum_expense_units'])
                    print(json.dumps(result),flush=True)
                finally:
                    with (a.output/'continuation-database.dump').open('xb') as out:
                        subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',db],stdout=out,check=True)
                return
            plan=accounting_preview(row,job,account)
            result={'kind':'item6-recovery-accounting-preview','original_run_id':identity['run_id'],'original_dump_sha256':archive_hash,'plan':plan,'mutation_performed':False,'provider_contacted':False,'acceptance_complete':False}
            if a.approve_plan:
                try:
                    result['reconciliation']=reconcile(store,a.attempt,review_sha256=a.approve_plan,original_dump_sha256=archive_hash,source_verification=__import__('vendor_config').verify_current_sources)
                    result['compute']=waive_unknown_compute(store,job['job_id']+':compute',original_dump_sha256=archive_hash,authorization_ref=a.waiver_authorization)
                    result['mutation_performed']=True
                finally:
                    with (a.output/'reconciled-database.dump').open('xb') as out:
                        subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',db],stdout=out,check=True)
            from isolation import RunIsolation
            RunIsolation.write(a.output/'accounting-result.json',result)
            print(json.dumps(result,indent=2))
    finally:
        subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)

if __name__=='__main__':main()
