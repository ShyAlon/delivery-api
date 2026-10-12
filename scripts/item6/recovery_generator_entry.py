#!/usr/bin/env python3
"""Dry-default successor-ledger generator review and separately authorized go.

No payment creation, cap reset, upstream replay or fabricated usage. Caller must
inspect native review and obtain owner's fresh exact paid approval before go.
"""
import argparse,hashlib,json,os,subprocess,shutil
from pathlib import Path
from bootstrap_database import bootstrap
from recovery_manifest import tree_digest
from recovery_generator import generator_review
from recovery_plan import digest
from recovery_unknown import JOB,ATTEMPT
from delivery_api.tenancy import TenantScope,tenant_context
from delivery_api.postgres_store import PostgresStore


def prepare(dump,identity,source,runtime,topup,*,phase_failure_recovery=False):
    original=json.loads(Path(identity).read_text());receipt=json.loads(Path(topup).read_text())
    owner='045bafdf-e35b-4b4b-93f2-0cd981b52806';sid='c8186df5-bfde-4499-86c3-a34823aa7f45';run='4eda39c0684945ff849b582c60dd91f8'
    if original.get('subject')!=owner or original.get('session_id')!='16caab89-5c55-4c36-af25-ba65105ed74f' or original.get('run_id')!=run or original.get('signature_verified') is not True or original.get('source_mode')!='mac_acceptance':raise ValueError('exact original verified isolated identity required')
    if receipt.get('kind')!='item6-sandbox-topup' or receipt.get('offer_id')!='credits-5' or receipt.get('amount_minor')!=500 or receipt.get('currency')!='USD' or receipt.get('credit_units')!=5*10**9 or receipt.get('sandbox') is not True or receipt.get('signature_verified') is not True or receipt.get('balance_before')!=362835621 or receipt.get('balance_after')!=5362835621 or not receipt.get('capture_id') or not receipt.get('event_id'):raise ValueError('exact completed five-credit sandbox top-up receipt required')
    root=Path(source)/'tenants'/owner/'sessions'/sid;checkpoint=generator_review(root,sid)
    if checkpoint['checkpoint_id']!='1f1c584c-4052-641a-8003-1a6caeab0d48' or checkpoint['architecture_partials_present']:raise ValueError('exact interrupted architecture checkpoint required')
    review={'version':3,'original_run_id':run,'successor_dump_sha256':hashlib.sha256(Path(dump).read_bytes()).hexdigest(),'original_failed_job':JOB,'task_id':'c11813e4a4b7','source_tree_sha256':tree_digest(source),'runtime_tree_sha256':tree_digest(runtime),'topup_receipt_sha256':hashlib.sha256(Path(topup).read_bytes()).hexdigest(),'generator_checkpoint':checkpoint,'maximum_expense_units':1000000000000,'conservative_unknown_expense_units':21394800000,'remaining_original_provider_cap_units':958338910000,'retail_balance_units':5362835621,'architecture_stage_regeneration':True,'upstream_replayed':False,'new_purchase':False,'uninterrupted_acceptance':False}
    if phase_failure_recovery:
        from recovery_phase_failure import JOB as failed,ARCHIVE,AUTHORITY
        review.update(version=4,original_failed_job=failed,phase_failure_recovery=True,
            phase_failure_archive_sha256=ARCHIVE,fourth_compute_waiver_authorization=AUTHORITY,
            no_provider_failure_job=failed)
    return review,digest(review)


def validate_successor(store,receipt,*,phase_failure_recovery=False):
    from recovery_continuation import require_reconciled_usage
    from completion_adapters import EvidenceReader
    rows=EvidenceReader(store).usage('c11813e4a4b7')
    if len(rows)!=120 or sum(r['state']=='settled' for r in rows)!=119:raise ValueError('exact successor provider attempts required')
    require_reconciled_usage(store,'c11813e4a4b7',rows,{JOB,'edad945ddadfa63b5bd73d5546a5bf69','9e04190ef41fac9f64e5f75b96e60b77'})
    held=[r for r in rows if r['state']!='settled']
    if len(held)!=1 or held[0]['attempt_id']!=ATTEMPT or held[0]['expense_reserved']!=21394800000:raise ValueError('exact waived unknown reserve required')
    if sum(r['expense_actual'] for r in rows if r['state']=='settled' and r['job_id']!='edad945ddadfa63b5bd73d5546a5bf69')!=20266290000:raise ValueError('original cumulative provider cap changed')
    compute=store.retail_compute_status('c11813e4a4b7')
    if compute.get('pending') or compute.get('unresolved') or compute.get('waived')!=(4 if phase_failure_recovery else 3):raise ValueError('exact audited unknown compute waivers required')
    with store._postgres.transaction() as c:
        account=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts').fetchone()
        if account['balance_units']!=5362835621 or account['units_per_credit']!=10**9:raise ValueError('exact topped-up balance required')
    if phase_failure_recovery:
        from recovery_phase_failure import validate_phase_failure
        validate_phase_failure(store,waived=True)
    # Payment tables are intentionally inaccessible to tenant identity. Caller
    # separately verifies with the restricted payment repository before go.


def main():
    os.umask(0o077);p=argparse.ArgumentParser()
    for key in ('dump','identity','source','runtime','topup','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--go');p.add_argument('--phase-failure-recovery',action='store_true');a=p.parse_args()
    review,sha=prepare(a.dump,a.identity,a.source,a.runtime,a.topup,phase_failure_recovery=a.phase_failure_recovery)
    print(json.dumps({'kind':'item6-generator-paid-review','review':review,'review_sha256':sha,'network_started':False}),flush=True)
    if a.go is None:return
    if a.go!=sha:raise ValueError('exact separately approved native paidreview required')
    if a.output.exists() or a.output.is_symlink():raise ValueError('fresh private continuation output required')
    a.output.mkdir(mode=0o700);data=Path('/tmp/item6-generator-pg');db='requi_item6_generator_4eda39c06849'
    if data.exists():raise ValueError('new ephemeral continuation database required')
    subprocess.run(['/usr/lib/postgresql/15/bin/initdb','-D',str(data),'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'-l','/tmp/generator-pg.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        from psycopg.conninfo import make_conninfo
        from delivery_api.payment_repository import PaymentRepository
        from delivery_api.paypal_adapter import PayPalBinding
        from recovery_run import run
        subprocess.run(['/usr/lib/postgresql/15/bin/createdb','-h','127.0.0.1','-p','55439','--template=template0',db],check=True)
        admin=make_conninfo(host='127.0.0.1',port=55439,user='requi',dbname=db);bootstrap(admin,'/app/migrations')
        subprocess.run(['/usr/lib/postgresql/15/bin/pg_restore','--exit-on-error','--data-only','--disable-triggers','--no-owner','--no-privileges','--dbname='+admin,str(a.dump)],check=True,stdout=subprocess.DEVNULL)
        original=json.loads(a.identity.read_text());scope=TenantScope(original['subject'],original['session_id'])
        store=PostgresStore(make_conninfo(host='127.0.0.1',port=55439,user='delivery_app',dbname=db))
        receipt=json.loads(a.topup.read_text());config=json.loads((a.runtime/'run.json').read_text())
        repo=PaymentRepository(make_conninfo(host='127.0.0.1',port=55439,user='delivery_payment_test',dbname=db),PayPalBinding(config['verified_sandbox_merchant'],config['sandbox_webhook_id'],False).ledger_binding)
        purchase=repo.get_purchase(receipt['purchase_id'])
        if purchase['owner_id']!=scope.owner_id or purchase['credit_posted'] is not True or purchase['state']!='fulfilled' or purchase['capture_id']!=receipt['capture_id'] or purchase['amount_minor']!=500 or purchase['credit_units']!=5*10**9:raise ValueError('exact durable payment receipt binding required')
        with repo.transaction() as c:
            events=c.execute("SELECT * FROM payment_events WHERE purchase_id=%s AND event_id=%s AND order_id=%s AND payment_id=%s AND action='paid' AND processor='paypal' AND merchant_account=%s AND live_mode=false",(receipt['purchase_id'],receipt['event_id'],purchase['order_id'],receipt['capture_id'],config['verified_sandbox_merchant'])).fetchall()
            if len(events)!=1:raise ValueError('exact verified capture webhook ledger receipt required')
        with tenant_context(scope):
            if not store.auth_session_active():raise ValueError('original restored auth session required')
            validate_successor(store,receipt,phase_failure_recovery=a.phase_failure_recovery)
        root=a.output/'working-run';shutil.copytree(a.source,root)
        if tree_digest(root)!=review['source_tree_sha256']:raise ValueError('copied original artifact tree changed')
        result=run(store=store,scope=scope,root=root,runtime=a.runtime,run_id=original['run_id'],task_id=review['task_id'],prior_job=review['original_failed_job'],output=a.output/'continuation',maximum_expense_units=review['maximum_expense_units'],generator_resume=review['generator_checkpoint'])
        print(json.dumps(result),flush=True)
    finally:
        try:
            with (a.output/'continuation-database.dump').open('xb') as out:subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',db],stdout=out,check=True)
        finally:subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
