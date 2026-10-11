#!/usr/bin/env python3
"""Exact isolated archive restore for approved sandbox top-up, never generator.

No host ports, live mode, manual grants, fresh signup, original order replay.
Always export changed private payment ledger even on timeout/failed webhook.
"""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
from bootstrap_database import bootstrap
from delivery_api.tenancy import TenantScope,tenant_context
from recovery_unknown import ARCHIVE,ATTEMPT,JOB


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    for name in ('dump','identity','runtime','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--offer',choices=['credits-5','credits-12','credits-25','credits-50'],required=True)
    p.add_argument('--approve-review');p.add_argument('--waiver-authorization');a=p.parse_args()
    from recovery_unknown import AUTHORITY
    if a.waiver_authorization not in (None,AUTHORITY):raise ValueError('exact unknown write-off authority required')
    from delivery_api.payment_offers import OFFERS
    pack,amount=OFFERS[a.offer]
    receipt=json.loads(a.identity.read_text());run_id='4eda39c0684945ff849b582c60dd91f8'
    if receipt.get('run_id')!=run_id or receipt.get('subject')!='045bafdf-e35b-4b4b-93f2-0cd981b52806' or receipt.get('session_id')!='16caab89-5c55-4c36-af25-ba65105ed74f' or receipt.get('signature_verified') is not True or receipt.get('source_mode')!='mac_acceptance':raise ValueError('exact verified original sandbox owner required')
    if a.dump.is_symlink() or hashlib.sha256(a.dump.read_bytes()).hexdigest()!=ARCHIVE:raise ValueError('exact stopped generator archive required')
    from recovery_manifest import tree_digest
    from recovery_plan import digest
    review={'kind':'item6-sandbox-topup-authorization','dump_sha256':ARCHIVE,'owner':receipt['subject'],'run_id':run_id,'offer':a.offer,'credit_units':pack*10**9,'amount_minor':amount,'currency':'USD','sandbox':True,'runtime_sha256':tree_digest(a.runtime),'generator_started':False,'production_effect':False,'unknown_writeoff_included':a.waiver_authorization is not None}
    print(json.dumps({'review':review,'review_sha256':digest(review),'network_started':False}),flush=True)
    if a.approve_review is None:return
    if a.approve_review!=digest(review):raise ValueError('exact separately reviewed pack/amount/runtime required')
    if a.output.exists() or a.output.is_symlink():raise ValueError('fresh private output required')
    a.output.mkdir(mode=0o700)
    data=Path('/tmp/item6-topup-pg');db='requi_item6_topup_'+run_id[:12]
    if data.exists():raise ValueError('new ephemeral top-up database required')
    subprocess.run(['/usr/lib/postgresql/15/bin/initdb','-D',str(data),'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'-l','/tmp/topup-pg.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        import psycopg,httpx
        from psycopg.conninfo import make_conninfo
        from delivery_api.postgres_store import PostgresStore
        from delivery_api.paypal_adapter import PayPalAdapter,PayPalBinding
        from delivery_api.payment_repository import PaymentRepository
        from network_boundaries import sandbox_clients
        from recovery_topup import prepare_topup,finish_topup
        from attended import Attended
        from isolation import RunIsolation
        subprocess.run(['/usr/lib/postgresql/15/bin/createdb','-h','127.0.0.1','-p','55439','--template=template0',db],check=True)
        admin=make_conninfo(host='127.0.0.1',port=55439,user='requi',dbname=db)
        bootstrap(admin,'/app/migrations')
        subprocess.run(['/usr/lib/postgresql/15/bin/pg_restore','--exit-on-error','--data-only','--disable-triggers','--no-owner','--no-privileges','--dbname='+admin,str(a.dump)],check=True,stdout=subprocess.DEVNULL)
        scope=TenantScope(receipt['subject'],receipt['session_id'])
        store=PostgresStore(make_conninfo(host='127.0.0.1',port=55439,user='delivery_app',dbname=db))
        def account(scope):
            with tenant_context(scope):
                if not store.auth_session_active():raise ValueError('original auth session required')
                with store._postgres.transaction() as c:
                    row=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s',(scope.owner_id,)).fetchone()
                    return dict(row)
        from recovery_unknown import validate_architecture_failure
        with tenant_context(scope):
            validate_architecture_failure(store)
            if a.waiver_authorization:
                from recovery_unknown import waive_unknown_provider
                from recovery_accounting import waive_unknown_compute
                with store._postgres.transaction():
                    provider=waive_unknown_provider(store,authorization_ref=a.waiver_authorization,archive_sha256=ARCHIVE)
                    compute=waive_unknown_compute(store,JOB+':compute',original_dump_sha256=ARCHIVE,authorization_ref=a.waiver_authorization)
                RunIsolation.write(a.output/'unknown-result.json',{'provider':provider,'compute':compute,'mutation_performed':True,'provider_contacted':False})
        if account(scope)!={'balance_units':362835621,'units_per_credit':10**9}:raise ValueError('exact existing balance required')
        config=json.loads((a.runtime/'run.json').read_text());secrets=json.loads((a.runtime/'credentials.json').read_text())
        binding=PayPalBinding(config['verified_sandbox_merchant'],config['sandbox_webhook_id'],False)
        client,transport=sandbox_clients(inner=httpx.HTTPTransport(retries=0),client_id=secrets['client_id'],client_secret=secrets['client_secret'],binding=binding,verified_app_merchant=config['verified_sandbox_merchant'])
        try:
            repo=PaymentRepository(make_conninfo(host='127.0.0.1',port=55439,user='delivery_payment_test',dbname=db),binding.ledger_binding)
            processor=PayPalAdapter(transport,binding);attended=Attended(a.output,lambda:scope.owner_id)
            prepared=prepare_topup(scope=scope,run_id=run_id,offer_id=a.offer,repository=repo,processor=processor,read_account=account)
            RunIsolation.write(a.output/'topup-review.json',prepared)
            attended.paypal(prepared['approval'])
            result=finish_topup(review=prepared,scope=scope,repository=repo,processor=processor,read_account=account,webhook_receipt=attended.webhook)
            RunIsolation.write(a.output/'topup-result.json',result)
            print(json.dumps(result),flush=True)
        finally:client.close()
    finally:
        try:
            with (a.output/'topup-database.dump').open('xb') as out:
                subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',db],stdout=out,check=True)
        finally:
            subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
