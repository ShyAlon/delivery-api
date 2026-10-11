#!/usr/bin/env python3
"""Exact offline restored-copy unknown write-off. No payments or model clients."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
from bootstrap_database import bootstrap
from recovery_unknown import ARCHIVE,ATTEMPT,JOB,AUTHORITY,validate_architecture_failure,waive_unknown_provider
from recovery_accounting import waive_unknown_compute
from delivery_api.tenancy import TenantScope,tenant_context
from delivery_api.postgres_store import PostgresStore


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    for name in ('dump','identity','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--authorization');a=p.parse_args()
    identity=json.loads(a.identity.read_text())
    if identity.get('subject')!='045bafdf-e35b-4b4b-93f2-0cd981b52806' or identity.get('session_id')!='16caab89-5c55-4c36-af25-ba65105ed74f' or identity.get('run_id')!='4eda39c0684945ff849b582c60dd91f8' or identity.get('signature_verified') is not True or identity.get('source_mode')!='mac_acceptance':raise ValueError('exact original isolated identity required')
    if a.dump.is_symlink() or hashlib.sha256(a.dump.read_bytes()).hexdigest()!=ARCHIVE:raise ValueError('exact architecture failure archive required')
    if a.authorization not in (None,AUTHORITY):raise ValueError('exact inspected owner approval required')
    if a.output.exists() or a.output.is_symlink():raise ValueError('fresh private output required')
    a.output.mkdir(mode=0o700);data=Path('/tmp/item6-unknown-pg');db='requi_item6_unknown_4eda39c06849'
    if data.exists():raise ValueError('fresh ephemeral restored copy required')
    subprocess.run(['/usr/lib/postgresql/15/bin/initdb','-D',str(data),'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'-l','/tmp/unknown-pg.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        from psycopg.conninfo import make_conninfo
        from isolation import RunIsolation
        subprocess.run(['/usr/lib/postgresql/15/bin/createdb','-h','127.0.0.1','-p','55439','--template=template0',db],check=True)
        admin=make_conninfo(host='127.0.0.1',port=55439,user='requi',dbname=db)
        bootstrap(admin,'/app/migrations')
        subprocess.run(['/usr/lib/postgresql/15/bin/pg_restore','--exit-on-error','--data-only','--disable-triggers','--no-owner','--no-privileges','--dbname='+admin,str(a.dump)],check=True,stdout=subprocess.DEVNULL)
        scope=TenantScope(identity['subject'],identity['session_id'])
        store=PostgresStore(make_conninfo(host='127.0.0.1',port=55439,user='delivery_app',dbname=db))
        with tenant_context(scope):
            if not store.auth_session_active():raise ValueError('original restored auth session required')
            before=validate_architecture_failure(store)
            result={'kind':'item6-unknown-failure-review','archive_sha256':ARCHIVE,'ledger':before,'customer_debit_units':0,'provider_expense_actual':None,'unknown_compute_waived':False,'provider_contacted':False,'mutation_performed':False}
            if a.authorization:
                # Provider and compute resolutions are one transaction.
                with store._postgres.transaction():
                    result['provider']=waive_unknown_provider(store,authorization_ref=a.authorization,archive_sha256=ARCHIVE)
                    result['compute']=waive_unknown_compute(store,JOB+':compute',original_dump_sha256=ARCHIVE,authorization_ref=a.authorization)
                result.update(mutation_performed=True,unknown_compute_waived=True)
            RunIsolation.write(a.output/'unknown-result.json',result)
            print(json.dumps(result),flush=True)
    finally:
        try:
            with (a.output/'unknown-database.dump').open('xb') as out:
                subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',db],stdout=out,check=True)
        finally:
            subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
