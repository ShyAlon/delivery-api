#!/usr/bin/env python3
"""Resume a stopped item 6 run inside a fresh disposable container.

Dry by default: restores the run's own database dump into a new throwaway database and prints
the stopped facts. Nothing is written to the run's evidence and no network call is made.
With the exact run go it also: asks for the operator's typed yes, writes off the two unknown
items, runs one new sandbox top-up, resumes the generator from its saved checkpoint and writes
a NEW database dump. The original dump and evidence names are never overwritten.
"""
import argparse,datetime,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path

PG='/usr/lib/postgresql/15/bin';PORT='55439';DATA=Path('/tmp/item6-pg')


def stamp():return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def say(text):print(f'{stamp()} | resume | {text}',flush=True)


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def plan_paths(config,number=1):
    """Resume N restores the dump of resume N-1 (resume 1 restores the stopped run's own dump) and writes new names."""
    root=Path(config['root']);parent=root.parent
    source=parent/'item6-database.dump' if number==1 else parent/('item6-database-resume-%d.dump'%(number-1))
    return {'root':root,'dump':source,'new_dump':parent/('item6-database-resume-%d.dump'%number),'output':parent/('resume-%d'%number),'number':number}


def preflight(config,run_id,paths,*,data=DATA):
    """Everything that can be refused without touching a database."""
    if not re.fullmatch(r'[a-f0-9]{32}',run_id):raise ValueError('exact run id required')
    if not re.fullmatch(r'requi_item6_[a-z0-9_]{8,60}',config['database_name']) or config['database_name']!='requi_item6_'+run_id:raise ValueError('database name must belong to this run')
    if config['database_host'] not in ('127.0.0.1','localhost','::1'):raise ValueError('loopback disposable database required')
    root=paths['root']
    if root.is_symlink() or not root.is_dir():raise ValueError('the stopped run directory must exist: '+str(root))
    if (root/'blocked.json').is_symlink() or not (root/'blocked.json').is_file():raise ValueError('a stopped run (blocked.json) is required')
    if paths['dump'].is_symlink() or not paths['dump'].is_file() or paths['dump'].stat().st_size==0:raise ValueError('the run database dump is missing')
    for name in ('new_dump','output'):
        if paths[name].exists() or paths[name].is_symlink():raise ValueError('this resume already ran: '+str(paths[name])+' exists; ask for a new resume number, never overwrite')
    if Path(data).exists():raise ValueError('new container database required')
    if paths.get('number',1)>1:return   # a later resume never buys a top-up unless the balance is not positive, which is refused below
    given=os.environ.get('ITEM6_WEBHOOK_DIR','')
    folder=Path(given) if given else None
    if folder is None or str(folder).rstrip('/')=='/private' or not folder.is_dir() or any(folder.glob('webhook-*.json')):
        raise ValueError('a fresh, empty webhook folder is required for the top-up (ITEM6_WEBHOOK_DIR); the first purchase export must not be reused')


def run(cmd,**kw):return subprocess.run(cmd,check=True,**kw)


def apply_ledger_schema(dsn,migrations='/app/migrations'):
    """pg_restore --clean puts the OLD dump's table definitions back, including the old state checks, so the
    released-attempt migration is applied again after the restore. The file is idempotent."""
    found=sorted(Path(migrations).glob('*_released_provider_attempts.sql'))
    if len(found)!=1:raise ValueError('the released-attempt migration is missing from the image (%d found)'%len(found))
    import psycopg
    with psycopg.connect(dsn) as c:c.execute(found[0].read_text(),prepare=False)
    return found[0].name


def start_database(config,dump):
    dbn=config['database_name']
    run([PG+'/initdb','-D',str(DATA),'--auth=trust','--encoding=UTF8','--no-locale'],stdout=subprocess.DEVNULL)
    run([PG+'/pg_ctl','-D',str(DATA),'-l','/tmp/item6-pg-server.log','-o','-h 127.0.0.1 -p '+PORT+' -k /tmp','start'],stdout=subprocess.DEVNULL)
    run([PG+'/createdb','-h','127.0.0.1','-p',PORT,'--template=template0',dbn])
    from bootstrap_database import bootstrap
    dsn='host=127.0.0.1 port='+PORT+' user=requi dbname='+dbn
    bootstrap(dsn,'/app/migrations')
    done=subprocess.run([PG+'/pg_restore','-h','127.0.0.1','-p',PORT,'-U','requi','-d',dbn,'--clean','--if-exists','--no-owner',str(dump)],capture_output=True,text=True)
    errors=[l for l in done.stderr.splitlines() if l.strip()]
    if done.returncode!=0 or errors:raise ValueError('restore of the run dump was not clean: exit %d, %d error lines; first: %s'%(done.returncode,len(errors),(errors[0][:160] if errors else '')))
    say('provider-attempt schema brought up to date after the restore: '+apply_ledger_schema(dsn))
    return dsn


def stop_database():
    run([PG+'/pg_ctl','-D',str(DATA),'stop','-m','fast'],stdout=subprocess.DEVNULL)


def archive_database(config,new_dump):
    fd=os.open(new_dump,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as out:
        run([PG+'/pg_dump','-h','127.0.0.1','-p',PORT,'-Fc',config['database_name']],stdout=out)


def read_facts(dsn):
    import psycopg
    from psycopg.rows import dict_row
    from recovery_resume import discover
    with psycopg.connect(dsn,row_factory=dict_row) as c:return discover(c)


def main(argv=None):
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--run-id',required=True)
    p.add_argument('--go');p.add_argument('--resume-number',type=int,default=1);p.add_argument('--ceiling-units',type=int);p.add_argument('--topup-offer',choices=('credits-12','credits-50'));p.add_argument('--topup-sequence',type=int,default=1)
    a=p.parse_args(argv)
    from mac_run import private_json
    config=private_json(a.config);paths=plan_paths(config,a.resume_number)
    preflight(config,a.run_id,paths)
    if a.go is not None and (a.go!=a.run_id or a.ceiling_units!=2*10**12):raise ValueError('exact run go and the $2.00 ceiling are required')
    say('restoring the run database dump into a new throwaway database (no model call, no network)')
    dump_sha=sha256_file(paths['dump'])
    started=False
    try:
        dsn=start_database(config,paths['dump']);started=True
        facts=read_facts(dsn)
        from recovery_resume import review_lines
        if a.go is None:
            for line in review_lines(facts,dump_sha):say(line)
            say('dry check passed; nothing was written. Balance is not positive, so a top-up purchase is needed before any paid step.' if facts['balance_units']<=0 else 'dry check passed; nothing was written.')
            if facts['balance_units']<=0 and paths['number']>1:raise ValueError('a later resume needs a positive balance; the earlier top-up is missing')
            return
        go(config,a.run_id,paths,facts,dump_sha,a.ceiling_units,topup_offer=a.topup_offer,topup_sequence=a.topup_sequence)
    finally:
        if started:
            if a.go is not None:
                try:archive_database(config,paths['new_dump']);say('database archived to '+str(paths['new_dump']))
                except Exception as error:say('ARCHIVE FAILED: '+type(error).__name__)
            stop_database()


# An extra top-up is only bought below this balance, so a repeated start can never buy a second pack.
EXTRA_TOPUP_BELOW_CREDITS=10


def go(config,run_id,paths,facts,dump_sha,ceiling_units,topup_offer=None,topup_sequence=1):
    import httpx,psycopg,jwt
    from psycopg.conninfo import make_conninfo
    from local_auth import DisposableSignup
    from mac_run import private_json
    from recovery_resume import confirm,confirm_extra,waive_extra_compute,waive_attempt,waive_compute,review_lines
    from recovery_topup import prepare_topup,finish_topup
    from recovery_continuation import execute_continuation
    from recovery_budget import RecoveryBudgetGateway
    from recovery_generator import generator_review
    from private_adapters import SandboxPurchaseAdapter
    from pipeline_executor import PrivatePipeline
    from attended import Attended
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import tenant_context,session_root
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.paypal_adapter import PayPalAdapter,PayPalBinding
    from delivery_api.payment_repository import PaymentRepository
    from delivery_api.producer_admission import WorkerAdmissionGateway
    from delivery_api.deepseek_usage import settlement_usage
    from delivery_api.admission_catalog import catalog_from_environment,ReviewedContextCeiling
    from delivery_api.retail_ledger import rates_from_snapshot
    from network_boundaries import sandbox_clients,deepseek_client_factory,deepseek_balance_reader,FixedOriginTransport
    from vendor_config import verify_current_sources
    from isolation import safe_model_route,parse_loopback_service
    secrets=private_json(config['paypal_secret_file'])
    if set(secrets)!={'client_id','client_secret','deepseek_api_key'}:raise ValueError('exact local credential fields required')
    safe_model_route('deepseek',config['model'],True)
    prices=private_json(config['catalog_file']);context=private_json(config['context_file'])
    if not prices or any(row.get('retail_units_per_credit')!=10**9 for row in prices):raise ValueError('exact D13 1000000000-unit prepaid scale required')
    for name in ('admin_service','tenant_service','payment_service'):
        d=parse_loopback_service(Path(config['service_file']).read_text(),config[name])
        if d['database_name']!=config['database_name'] or d['host']!=config['database_host'] or d['port']!=int(PORT):raise ValueError('service destination differs from the restored run database')
    # The operator's own typed answer is the only authority for the write-offs. A later resume finds them already written off.
    if facts['already_written_off']:
        for line in review_lines(facts,dump_sha):say(line)
        ref=None
        extra_ref=confirm_extra(facts,dump_sha,run_id) if facts['extra_compute'] else None
    else:ref=confirm(facts,dump_sha,run_id);extra_ref=None
    os.environ['PGSERVICEFILE']=config['service_file'];os.environ['DEEPSEEK_API_KEY']=secrets['deepseek_api_key']
    for key in list(os.environ):
        if key.startswith(('INTERVIEW_ENGINE_CATEGORIZER_','VMODEL_REPLAY','VMODEL_CAP_')) or key in ('VMODEL_LLM_MAX_TOKENS','VMODEL_TENANT_OWNER','INTERVIEW_ENGINE_TENANTS'):os.environ.pop(key,None)
    os.environ['INTERVIEW_ENGINE_CATEGORIZERS']='llm,rules';os.environ.pop('TYPESAFE_API_KEY',None)
    os.environ['VMODEL_PRICE_TABLE']=str(paths['root']/'diagnostic-prices.json')
    admin=lambda:psycopg.connect(make_conninfo(service=config['admin_service']))
    store=PostgresStore(make_conninfo(service=config['tenant_service']))
    signup=ResumeSignup(admin_connection=admin,mode='mac_acceptance',subject=facts['owner_id'],session=facts['auth_session_id'])
    scope=signup.verifier.verify(signup()['token'])
    if scope.owner_id!=facts['owner_id'] or scope.session_id!=facts['auth_session_id']:raise ValueError('restored identity differs from the stopped run')
    with tenant_context(scope):
        if store.auth_session_active() is not True:raise ValueError('active restored session required')
        if ref is None:say('write-offs already done in an earlier resume: not repeated')
        else:
            say('write-off 1 of 2: never-started provider call '+facts['attempt_id'])
            say('result: '+json.dumps(waive_attempt(store,facts,authorization_ref=ref,dump_sha256=dump_sha),sort_keys=True,default=str))
            say('write-off 2 of 2: unmeasured compute of the failed job')
            say('result: '+json.dumps(waive_compute(store,facts,authorization_ref=ref,dump_sha256=dump_sha),sort_keys=True,default=str))
        for item in facts['extra_compute'] if extra_ref else []:
            say('write-off of unmeasured compute of earlier failed resume job '+item['job_id']+' ('+item['segment_key']+')')
            say('result: '+json.dumps(waive_extra_compute(store,item,authorization_ref=extra_ref,dump_sha256=dump_sha),sort_keys=True,default=str))
    binding=PayPalBinding(config['verified_sandbox_merchant'],config['sandbox_webhook_id'],False)
    paypal_client,transport=sandbox_clients(inner=httpx.HTTPTransport(retries=0),client_id=secrets['client_id'],client_secret=secrets['client_secret'],binding=binding,verified_app_merchant=config['verified_sandbox_merchant'])
    balance_client=httpx.Client(transport=FixedOriginTransport(httpx.HTTPTransport(retries=0),'https://api.deepseek.com',lambda method,path:method=='GET' and path=='/user/balance'),trust_env=False,follow_redirects=False)
    try:
        repository=PaymentRepository(make_conninfo(service=config['payment_service']),binding.ledger_binding)
        def read_account(scope):
            with admin() as c:
                row=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s',(scope.owner_id,)).fetchone()
            return {'balance_units':row[0],'units_per_credit':row[1]}
        processor=PayPalAdapter(transport,binding)
        extra=topup_offer is not None
        if extra and facts['balance_units']>=EXTRA_TOPUP_BELOW_CREDITS*10**9:
            say('credit balance is already '+str(facts['balance_units'])+' units (at least '+str(EXTRA_TOPUP_BELOW_CREDITS)+' credits): the extra top-up is skipped')
            extra=False
        if extra and paths['number']==1:raise ValueError('an extra top-up applies to a later resume')
        if extra and topup_sequence<2:raise ValueError('an extra top-up needs its own purchase sequence (2 or more)')
        if facts['balance_units']>0 and not extra:
            say('credit balance is already positive ('+str(facts['balance_units'])+' units): no new top-up purchase')
        elif paths['number']>1 and not extra:raise ValueError('a later resume needs a positive balance; the earlier top-up is missing')
        else:
            attended=Attended(paths['root'].parent,lambda:scope.owner_id)
            review=prepare_topup(scope=scope,run_id=run_id,offer_id=topup_offer or 'credits-12',repository=repository,processor=processor,read_account=read_account,sequence=topup_sequence if extra else 1)
            say('top-up purchase '+review['purchase_id']+': balance before '+str(review['balance_before']))
            attended.paypal(review['approval'])
            waiter=SandboxPurchaseAdapter(type('Identity',(),{'scope':scope})(),repository=repository,processor=processor,approve=attended.paypal,webhook_receipt=attended.webhook,read_balance=lambda s:read_account(s)['balance_units'],units_per_credit=10**9)
            waiter.await_approval(repository.get_purchase(review['purchase_id'])['order_id'])
            topup=finish_topup(review=review,scope=scope,repository=repository,processor=processor,read_account=read_account,webhook_receipt=attended.webhook)
            say('top-up done: balance after '+str(topup['balance_after']))
            if topup['balance_after']<=0:raise ValueError('positive balance required before any paid step')
        # The paid resume starts only after the top-up is confirmed. The ceiling counts all provider spend in this run.
        catalog=catalog_from_environment({'VMODEL_REVIEWED_MODEL_PRICES':json.dumps(prices)},bounders={('serialized','deepseek',config['model']):ReviewedContextCeiling(**context)})
        rates=rates_from_snapshot(prices[0]['retail_formula'])
        reader=deepseek_balance_reader(client=balance_client,api_key=secrets['deepseek_api_key'])
        def gateway(cap,job):
            inner=WorkerAdmissionGateway(store,job,resolve_serialized=catalog.resolve_serialized,measure_raw_usage=settlement_usage,require_prepaid=True,retail_rates=rates,
                verify_provider_snapshot=lambda:verify_current_sources(prices[0]['provider_snapshot']),provider_balance=reader)
            return RecoveryBudgetGateway(inner,original_job_ids=[],maximum_expense_units=ceiling_units)
        accept=None
        def pipeline_factory(att):
            return PrivatePipeline(store=store,artifacts_base=paths['root'],model=config['model'],gateway_factory=gateway,
                generator_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='spec_generator'),
                interview_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='interview_engine'),
                answer_input=att.input,review_gate=att.gate,accept_script=accept,progress=lambda state,text:print(f'{stamp()} | {state} | {text}',flush=True))
        with tenant_context(scope):
            cap=WorkerCapability.capture(store,facts['task_id'])
            resume=generator_review(session_root(paths['root'],scope,cap.session_id),cap.session_id)
        stage='assemble' if resume.get('review_complete_present') else 'review' if resume.get('review_partials_present') else 'tests' if resume.get('tests_partials_present') else None
        if stage is None:raise ValueError('saved tests fills, saved review chunks or a finished review are required for this resume')
        if stage=='assemble':
            from accept_findings import build_script,script_sha,phrase,summary_lines
            import findings
            with tenant_context(scope):
                spec_dir=session_root(paths['root'],scope,cap.session_id)/'spec'
            ledger=json.loads((spec_dir/'findings-ledger.json').read_text())
            if hashlib.sha256(json.dumps(ledger,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=resume['ledger_sha256']:raise ValueError('the saved findings ledger differs from the checkpoint; nothing was changed')
            script=build_script(ledger)
            if not script['dispositions']:raise ValueError('assembly was blocked but no open finding exists; look at the ledger before resuming')
            say('the review finished; assembly is blocked by its findings. Nothing has been spent in this step and no model is called.')
            for line in summary_lines(ledger,script):say(line)
            say('each entry is one exact finding id bound to its artifact revision; rationale: '+script['dispositions'][0]['rationale'])
            want=phrase(script)
            typed=input(stamp()+' | type exactly: '+want+'  (anything else stops with no change): ')
            if typed.strip()!=want:raise ValueError('the accept phrase was not typed exactly; nothing was changed')
            findings.record('accepted-review-findings',str(len(script['dispositions']))+' open review findings accepted by the typed phrase '+want+' (script sha256 '+script_sha(script)+'); recorded as accepted risk, not fixed')
            accept=script
        say('resuming the generator from its saved '+stage+' checkpoint '+resume['checkpoint_id'])
        result=execute_continuation(store=store,scope=scope,root=paths['root'],config={},run_id=run_id,task_id=facts['task_id'],prior_job=facts['failed_job'],
            output=paths['output'],pipeline_factory=pipeline_factory,original_failed_jobs=facts['failed_jobs'],pending_review=None,generator_resume=resume)
        print(json.dumps(result),flush=True)
    finally:
        paypal_client.close();balance_client.close()


def _resume_signup():
    import jwt
    from local_auth import DisposableSignup
    class ResumeSignup(DisposableSignup):
        """Signs a fresh token for the stopped run's own owner and session rows. Creates no account."""
        def __init__(self,*,subject,session,**kw):
            super().__init__(**kw);self.subject=subject;self.session=session
        def __call__(self):
            if self.used:raise ValueError('no signup replay')
            self.used=True
            with self.connect() as c:
                if c.execute('SELECT 1 FROM auth.sessions WHERE id=%s AND user_id=%s',(self.session,self.subject)).fetchone() is None:raise ValueError('the stopped run session rows are required')
            now=int(time.time())
            token=jwt.encode({'sub':self.subject,'session_id':self.session,'role':'authenticated','iat':now,'exp':now+86400,'iss':self.issuer+'/auth/v1','aud':'authenticated'},self.key,algorithm='ES256',headers={'kid':'item6-ephemeral'})
            return {'token':token,'fresh_signup':False,'source_mode':self.mode}
    return ResumeSignup


def ResumeSignup(**kw):return _resume_signup()(**kw)


def failure_text(error):
    import traceback
    where=' at '+' <- '.join('%s:%d %s'%(f.filename,f.lineno,f.name) for f in reversed(traceback.extract_tb(error.__traceback__)[-3:]))
    # File and line names only: no exception text except for a missing or unreadable file (its path) and our own refusals.
    detail=str(error)[:300] if isinstance(error,ValueError) else type(error).__name__+((' file '+str(error.filename)[:200]) if isinstance(error,OSError) and error.filename else '')
    return 'Item6 resume stopped: '+detail+where+'. Evidence preserved; no blind retry.'


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(failure_text(error),file=sys.stderr);raise SystemExit(1) from None
