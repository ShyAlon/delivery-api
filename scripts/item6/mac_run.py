#!/usr/bin/env python3
"""Dry-default isolated Linux-on-Mac acceptance entry point. No public app import."""
import argparse,datetime,json,os,sys
from pathlib import Path


def private_json(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077 or path.stat().st_size>1024*1024:
        raise ValueError('private bounded local configuration file required')
    return json.loads(path.read_text())


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path);parser.add_argument('--run-id',required=True)
    parser.add_argument('--go',help='Exact separately approved run id; omission stays dry')
    args=parser.parse_args();config=private_json(args.config)
    from isolation import RunIsolation,safe_model_route,parse_loopback_service
    required={'root','database_name','database_host','service_file','admin_service','tenant_service','payment_service',
        'model','catalog_file','context_file','paypal_secret_file','verified_sandbox_merchant','sandbox_webhook_id','operator_registry_version'}
    if not isinstance(config,dict) or set(config)!=required:raise ValueError('exact reviewed isolated run configuration required')
    isolation=RunIsolation(Path(config['root']),config['database_name'],config['database_host'],args.run_id,mode='mac_acceptance')
    isolation.validate()
    if not Path('/proc/self/fd').is_dir():raise ValueError('Linux container required for pinned SQLite revision readers')
    for name in ('admin_service','tenant_service','payment_service'):
        path=Path(config['service_file'])
        if path.is_symlink() or path.stat().st_mode&0o077:raise ValueError('private service file required')
        destination=parse_loopback_service(path.read_text(),config[name])
        if destination['database_name']!=isolation.database_name or destination['host']!=isolation.database_host:raise ValueError('service destination differs from reviewed run')
    prices=private_json(config['catalog_file']);context=private_json(config['context_file']);secrets=private_json(config['paypal_secret_file'])
    if set(secrets)!={'client_id','client_secret','deepseek_api_key'}:raise ValueError('exact local credential fields required')
    safe_model_route('deepseek',config['model'],True)
    if not config['operator_registry_version']:raise ValueError('reviewed operator registry version required')
    if not prices or any(row.get('retail_units_per_credit')!=10**9 for row in prices):raise ValueError('exact D13 1000000000-unit prepaid scale required')
    if any('retail_formula' not in row or 'provider_snapshot' not in row for row in prices):raise ValueError('D13 formula and generated vendor snapshot required')
    from delivery_api.admission_catalog import catalog_from_environment,ReviewedContextCeiling
    ceiling=ReviewedContextCeiling(**context)
    if ceiling.model!=config['model']:raise ValueError('exact reviewed effective model ceiling required')
    catalog=catalog_from_environment({'VMODEL_REVIEWED_MODEL_PRICES':json.dumps(prices)},bounders={('serialized','deepseek',config['model']):ceiling})
    from delivery_api.launch_admission_policy import validate_launch_quote
    # Exercise quote validation without connecting a provider or a database.
    quote,_=catalog.resolve_serialized(stage='preflight',provider='deepseek',model=config['model'],
        serialized_request=json.dumps({'model':config['model'],'max_tokens':1}).encode(),max_output_tokens=1)
    validate_launch_quote(quote)
    from delivery_api.paypal_adapter import PayPalBinding
    PayPalBinding(config['verified_sandbox_merchant'],config['sandbox_webhook_id'],False)
    print(json.dumps({'kind':'item6-dry-preflight','run_id':args.run_id,'mode':isolation.mode,'test_auth':'disposable-local-signed',
        'model':config['model'],'network_started':False,'database_changed':False,'launch_authorized':False}),flush=True)
    if args.go is None:return
    if args.go!=args.run_id:raise ValueError('exact separately approved run go required')
    import httpx,psycopg
    from psycopg.conninfo import make_conninfo
    from local_auth import DisposableSignup
    from private_adapters import IdentityAdapter,OperatorAdapter,SandboxPurchaseAdapter
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.paypal_adapter import PayPalAdapter,PayPalBinding
    from delivery_api.payment_repository import PaymentRepository
    from delivery_api.producer_admission import WorkerAdmissionGateway
    from delivery_api.deepseek_usage import settlement_usage
    from network_boundaries import sandbox_clients,deepseek_client_factory
    from pipeline_executor import PrivatePipeline
    from revision_executor import PrivateGateEdit
    from completion_adapters import EvidenceReader,FinalArtifactsAdapter,RecoveryAdapter
    from artifact_receipts import PipelineReceiptAdapter
    from attended import Attended
    from chain import AcceptanceChain
    os.environ['PGSERVICEFILE']=config['service_file'];os.environ['DEEPSEEK_API_KEY']=secrets['deepseek_api_key']
    # This isolated run's keys never route to a categorization service or other provider.
    for key in list(os.environ):
        if key.startswith(('INTERVIEW_ENGINE_CATEGORIZER_','VMODEL_REPLAY','VMODEL_CAP_')) or key in ('VMODEL_LLM_MAX_TOKENS','VMODEL_TENANT_OWNER','INTERVIEW_ENGINE_TENANTS'):
            os.environ.pop(key,None)
    os.environ['INTERVIEW_ENGINE_CATEGORIZERS']='llm,rules'
    os.environ.pop('TYPESAFE_API_KEY',None)
    def admin():return psycopg.connect(make_conninfo(service=config['admin_service']))
    store=PostgresStore(make_conninfo(service=config['tenant_service']))
    signup=DisposableSignup(admin_connection=admin,mode=isolation.mode)
    identity=IdentityAdapter(signup=signup,verifier=signup.verifier,store=store)
    attended=Attended(isolation.root,lambda:identity.scope.owner_id)
    def provision_registry():
        scope=identity.scope
        with admin() as c:
            enabled=c.execute('SELECT subject FROM operator_principals WHERE enabled').fetchall()
            if enabled:raise ValueError('fresh disposable operator registry required')
            c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,%s,ARRAY['payments.read'],true)",(scope.owner_id,config['operator_registry_version']))
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000000000,0)',(scope.owner_id,))
            c.execute('INSERT INTO admission_limits(owner_id,config_version,period_seconds,rate_window_seconds,concurrency_max) VALUES(%s,%s,86400,60,3)',(scope.owner_id,config['operator_registry_version']))
        return OperatorPrincipal(scope.owner_id,config['operator_registry_version'],frozenset({'payments.read'}))
    operator=OperatorAdapter(identity,store=store,read_reviewed_registry=provision_registry)
    binding=PayPalBinding(config['verified_sandbox_merchant'],config['sandbox_webhook_id'],False)
    paypal_client,transport=sandbox_clients(inner=httpx.HTTPTransport(retries=0),client_id=secrets['client_id'],client_secret=secrets['client_secret'],binding=binding,verified_app_merchant=config['verified_sandbox_merchant'])
    repository=PaymentRepository(make_conninfo(service=config['payment_service']),binding.ledger_binding)
    def balance(scope):
        with admin() as c:return c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(scope.owner_id,)).fetchone()[0]
    purchase=SandboxPurchaseAdapter(identity,repository=repository,processor=PayPalAdapter(transport,binding),approve=attended.paypal,webhook_receipt=attended.webhook,read_balance=balance,units_per_credit=10**9)
    from delivery_api.retail_ledger import rates_from_snapshot
    retail_rates=rates_from_snapshot(prices[0]['retail_formula'])
    from vendor_config import verify_current_sources
    def verify_vendor():return verify_current_sources(prices[0]['provider_snapshot'])
    from network_boundaries import deepseek_balance_reader,FixedOriginTransport
    balance_client=httpx.Client(transport=FixedOriginTransport(httpx.HTTPTransport(retries=0),'https://api.deepseek.com',lambda method,path:method=='GET' and path=='/user/balance'),trust_env=False,follow_redirects=False)
    provider_balance=deepseek_balance_reader(client=balance_client,api_key=secrets['deepseek_api_key'])
    def gateway(cap,job):return WorkerAdmissionGateway(store,job,resolve_serialized=catalog.resolve_serialized,measure_raw_usage=settlement_usage,require_prepaid=True,retail_rates=retail_rates,verify_provider_snapshot=verify_vendor,provider_balance=provider_balance)
    def progress(state,text):print(f'{datetime.datetime.now().astimezone().isoformat(timespec="seconds")} | {state} | {text}',flush=True)
    # Shared diagnostic table is explicit for this selected model. Cache-aware
    # source-of-truth accounting remains in the exact quoted raw-usage ledger.
    table=isolation.root/'diagnostic-prices.json'
    selected=catalog.prices[('deepseek',config['model'])]
    original_identity=identity
    def identity_stage(scope,previous):
        receipt=original_identity(scope,previous)
        RunIsolation.write(table,{config['model']:[selected.expense_input*1_000_000/selected.expense_units_per_usd,selected.expense_output*1_000_000/selected.expense_units_per_usd]})
        os.environ['VMODEL_PRICE_TABLE']=str(table)
        return receipt
    pipeline=PrivatePipeline(store=store,artifacts_base=isolation.root,model=config['model'],gateway_factory=gateway,
        generator_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='spec_generator'),
        interview_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='interview_engine'),
        answer_input=attended.input,review_gate=attended.gate,progress=progress)
    evidence=EvidenceReader(store)
    adapters={'identity':identity_stage,'operator':operator,'sandbox_purchase':purchase,
        'pipeline':PipelineReceiptAdapter(identity,store=store,artifacts_base=isolation.root,execute_pipeline=pipeline,read_completed_jobs=evidence.jobs,read_usage=evidence.usage),
        'gate_edit':PrivateGateEdit(identity,store=store,artifacts_base=isolation.root,pipeline=pipeline,choose_change=attended.change,accept_fresh_review=attended.approve_revision),
        'final_artifacts':FinalArtifactsAdapter(identity,store=store,artifacts_base=isolation.root),
        'recovery':RecoveryAdapter(identity,store=store,read_evidence=evidence)}
    try:
        result=AcceptanceChain(isolation,adapters=adapters,progress=lambda line:print(line,flush=True)).execute(run_go=args.go)
        print(json.dumps(result),flush=True)
    finally:
        paypal_client.close();balance_client.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Item6 stopped. Preserve private local evidence. No blind retry, launch, or cleanup.',file=sys.stderr)
        raise SystemExit(1) from None
