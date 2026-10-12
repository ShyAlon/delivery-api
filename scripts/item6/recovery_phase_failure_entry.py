#!/usr/bin/env python3
"""Offline exact fourth-compute waiver only, no generator or payment calls."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
from bootstrap_database import bootstrap
from delivery_api.tenancy import TenantScope,tenant_context
from delivery_api.postgres_store import PostgresStore
from recovery_phase_failure import ARCHIVE,AUTHORITY,validate_phase_failure,apply_phase_failure_waiver
from isolation import RunIsolation


def main():
    os.umask(0o077);p=argparse.ArgumentParser()
    for k in ('dump','identity','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--authorization',required=True);a=p.parse_args()
    if a.authorization!=AUTHORITY or a.dump.is_symlink() or hashlib.sha256(a.dump.read_bytes()).hexdigest()!=ARCHIVE:raise ValueError('exact owner-approved crash archive required')
    original=json.loads(a.identity.read_text())
    if original.get('subject')!='045bafdf-e35b-4b4b-93f2-0cd981b52806' or original.get('session_id')!='16caab89-5c55-4c36-af25-ba65105ed74f' or original.get('run_id')!='4eda39c0684945ff849b582c60dd91f8' or original.get('signature_verified') is not True or original.get('source_mode')!='mac_acceptance':raise ValueError('exact original verified identity required')
    if a.output.exists() or a.output.is_symlink():raise ValueError('fresh output required; preserve prior evidence')
    a.output.mkdir(mode=0o700);bin='/usr/lib/postgresql/15/bin/';data='/tmp/phase-pg';name='requi_item6_phase_failure_waiver'
    subprocess.run([bin+'initdb','-D',data,'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run([bin+'pg_ctl','-D',data,'-l','/tmp/pg.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
    restored=False
    try:
        subprocess.run([bin+'createdb','-h','127.0.0.1','-p','55439','--template=template0',name],check=True)
        admin='host=127.0.0.1 port=55439 user=requi dbname='+name;bootstrap(admin,'/app/migrations')
        subprocess.run([bin+'pg_restore','--exit-on-error','--data-only','--disable-triggers','--no-owner','--no-privileges','--dbname='+admin,str(a.dump)],check=True,stdout=subprocess.DEVNULL);restored=True
        store=PostgresStore(admin.replace('user=requi','user=delivery_app'))
        with tenant_context(TenantScope(original['subject'],original['session_id'])):
            if not store.auth_session_active():raise ValueError('original restored auth session required')
            before=validate_phase_failure(store,waived=False)
            from recovery_generator_entry import validate_successor
            # Validate existing cap/usage before allowing compute-only recovery.
            from recovery_continuation import require_reconciled_usage
            from completion_adapters import EvidenceReader
            from recovery_phase_failure import ORIGINAL_JOBS,JOB
            rows=EvidenceReader(store).usage('c11813e4a4b7')
            if len(rows)!=120 or sum(r['state']=='settled' for r in rows)!=119:raise ValueError('unchanged provider ledger required')
            require_reconciled_usage(store,'c11813e4a4b7',rows,{j for j,state in ORIGINAL_JOBS.items() if state=='failed'})
            result=apply_phase_failure_waiver(store,archive_sha256=ARCHIVE,authorization_ref=a.authorization)
            after=validate_phase_failure(store,waived=True)
        evidence={'kind':'item6-phase-failure-compute-waiver','original_dump_sha256':ARCHIVE,'authorization_ref':AUTHORITY,'before':before,'result':result,'after':after,'provider_contacted':False,'generator_started':False,'new_purchase':False,'original_evidence_mutated':False}
        RunIsolation.write(a.output/'waiver-result.json',evidence);print(json.dumps(evidence,indent=2))
    finally:
        try:
            if restored:
                with (a.output/'phase-reconciled-database.dump').open('xb') as out:subprocess.run([bin+'pg_dump','-h','127.0.0.1','-p','55439','-Fc',name],stdout=out,check=True)
        finally:subprocess.run([bin+'pg_ctl','-D',data,'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
