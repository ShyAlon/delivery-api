"""Generated snapshot compiler. No founder copies vendor price/band fields."""
import datetime,hashlib,json,time,urllib.error
from pathlib import Path
from decimal import Decimal
from isolation import RunIsolation
from model_data import parse,fetch,PRICING_URL,API_URL
from delivery_api.provider_snapshot import ProviderSnapshot
from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT

RETAIL={'PRICE_PER_TOKEN','PRICE_PER_10MS','USD_PER_CREDIT'}
BASE={'MODEL','PAYPAL_CLIENT_ID','PAYPAL_CLIENT_SECRET','DEEPSEEK_API_KEY','SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID'}
REMOVED={'PRICE_SOURCE','CONTEXT_SOURCE','PRICING_BAND','INPUT_CACHE_HIT_USD_PER_MILLION','INPUT_CACHE_MISS_USD_PER_MILLION','OUTPUT_USD_PER_MILLION','CONTEXT_TOKENS','MAX_OUTPUT_TOKENS','RETAIL_INPUT_UNITS_PER_TOKEN','RETAIL_OUTPUT_UNITS_PER_TOKEN'}


def migrate_env(root):
    from single_config import root_check,load_env,write_env
    root,_=root_check(root);values,_=load_env(root)
    candidate={k:v for k,v in values.items() if k in BASE|RETAIL}
    target=root/'private'/('env-d13-candidate-'+hashlib.sha256((root/'private/.env').read_bytes()).hexdigest()[:16])
    write_env(target,candidate)
    return {'proposed_env_file':str(target),'old_source_preserved':True,'removed_fields':sorted(REMOVED & values.keys()),'retail_overrides':'optional; missing uses existing code defaults','model_role':'default/attended fixture only; selected per phase in product','run_started':False}


def generate_snapshot(root,model,fetcher=fetch):
    from single_config import root_check
    root,_=root_check(root)
    sources={u:fetcher(u) for u in (PRICING_URL,API_URL)}
    snapshot=parse(sources[PRICING_URL],sources[API_URL],model)
    snapshot['parser_version']='deepseek-html-v2';snapshot['fetched_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    snapshot['sources']=[{'url':u,'sha256':hashlib.sha256(body.encode()).hexdigest()} for u,body in sources.items()]
    # Delete obsolete manual pricing instructions, not source evidence.
    snapshot.pop('manual_review_required',None);snapshot.pop('catalog_proposals',None)
    snapshot['protective_run_context_tokens']=128000
    snapshot['protective_ceiling_source']='Owner reply Oct10 18:51 to proposed 128k run ceiling; not a vendor model maximum'
    checked=ProviderSnapshot(snapshot)
    target=root/'private'/('vendor-snapshot-'+checked.id+'.json')
    RunIsolation.write(target,snapshot)
    return {'snapshot_file':str(target),'snapshot_sha256':checked.id,'sources':snapshot['sources'],'protective_run_context_tokens':128000,'run_started':False,'reviewed':False}


def load_snapshot(root,model):
    from single_config import private_json
    paths=list((root/'private').glob('vendor-snapshot-*.json'))
    if len(paths)!=1:raise ValueError('one generated current vendor snapshot required; preserve older evidence outside active snapshot selection')
    snapshot=private_json(paths[0]);checked=ProviderSnapshot(snapshot)
    if snapshot['model']!=model:raise ValueError('phase/default model differs from generated vendor snapshot')
    checked.fresh(datetime.datetime.now(datetime.timezone.utc))
    if paths[0].name!='vendor-snapshot-'+checked.id+'.json':raise ValueError('vendor snapshot hash/name differs')
    return snapshot,checked


def build_runtime(root,data,identity):
    from single_config import SECRET,unresolved
    from delivery_api.paypal_adapter import PayPalBinding
    from review_diagnostics import require_bindings
    require_bindings(data)
    if any(unresolved(data.get(k)) for k in BASE):raise ValueError('required local sandbox bindings, keys and fixture model missing')
    if REMOVED & data.keys():raise ValueError('legacy manual provider/retail fields remain; review migrate-d13 candidate before compile')
    rates=RetailRates.from_environment({k:data[k] for k in RETAIL if k in data})
    snapshot,checked=load_snapshot(root,data['MODEL']);ceiling=checked.ceiling()
    price={'provider':'deepseek','model':data['MODEL'],'source':PRICING_URL,'price_version':'vendor-'+checked.id,'expense_input':ceiling['cache_miss'],'expense_cache_hit':ceiling['cache_hit'],'expense_cache_miss':ceiling['cache_miss'],'expense_output':ceiling['output'],'expense_units_per_usd':10**12,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_input':int(rates.price_per_token*UNITS_PER_CREDIT),'retail_output':int(rates.price_per_token*UNITS_PER_CREDIT),'retail_formula':rates.snapshot(),'provider_snapshot':snapshot,'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}
    context={'model':data['MODEL'],'context_tokens':128000,'maximum_output_tokens':min(128000,snapshot['context_proposal']['maximum_output_tokens']),'source':snapshot['protective_ceiling_source'],'bound_version':'owner-run-128000-v1','protective_run_ceiling':True}
    PayPalBinding(data['SANDBOX_MERCHANT_ID'],data['SANDBOX_WEBHOOK_ID'],False)
    if any(not isinstance(data[k],str) or not data[k] for k in SECRET):raise ValueError('local credential fields required')
    run={'root':'/evidence/run','database_name':identity['database_name'],'database_host':'127.0.0.1','service_file':'/private/services.conf','admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment','model':data['MODEL'],'catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json','verified_sandbox_merchant':data['SANDBOX_MERCHANT_ID'],'sandbox_webhook_id':data['SANDBOX_WEBHOOK_ID'],'operator_registry_version':'item6-local-'+identity['run_id'][:12]}
    return {'catalog.json':[price],'context.json':context,'run.json':run,'credentials.json':{'client_id':data['PAYPAL_CLIENT_ID'],'client_secret':data['PAYPAL_CLIENT_SECRET'],'deepseek_api_key':data['DEEPSEEK_API_KEY']}}


def fetch_verified_source(url,fetcher=fetch,sleeper=time.sleep):
    # Public GET only. Never retry model dispatch or mismatched source content.
    for attempt in range(3):
        try:return fetcher(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==2:raise
        except (urllib.error.URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        sleeper((1,2)[attempt])


_GOOD={}
FACT_KEYS=('model','model_version','published_usd_per_million','published_period_rule','raw_context_limit','raw_output_limit')


def check_max_age_minutes():
    """One setting, one place: VENDOR_CHECK_MAX_AGE_MINUTES (default 60, 1 to 60)."""
    import os
    raw=os.environ.get('VENDOR_CHECK_MAX_AGE_MINUTES','60')
    try:minutes=int(raw)
    except ValueError:raise ValueError('VENDOR_CHECK_MAX_AGE_MINUTES must be a whole number of minutes from 1 to 60') from None
    if not 1<=minutes<=60:raise ValueError('VENDOR_CHECK_MAX_AGE_MINUTES must be a whole number of minutes from 1 to 60')
    return minutes


GRACE_MINUTES=60
# A page that no longer parses only warns when the problem is layout; anything about rates, periods, limits or conversion still stops.
HARD_PARSE_WORDS=('rate','period','limit','output','conversion','USD','selected model')
WARN_ONLY_FACTS=('model_version',)   # facts that never change what a call costs; every other pinned fact is money or a limit
_WARNED={}


def _note(log,kind,text):
    """One line with an ISO time, and one entry in the findings list (best effort, never a reason to stop)."""
    line=datetime.datetime.now(datetime.timezone.utc).isoformat()+' WARNING '+text
    log(line)
    try:
        from findings import record
        record(kind,text)
    except Exception:pass


def verify_current_sources(snapshot,fetcher=fetch,*,cached=True,clock=time.monotonic,log=None):
    """Before a new contact/settlement; public reads, never provider inference.

    A good check is reused for check_max_age_minutes(). Within GRACE_MINUTES of the last good check
    (this process, or the read that pinned the snapshot) a page that cannot be read, no longer parses,
    or differs only in a fact that does not change cost (model_version) is a logged warning and a
    finding, and the run continues. A changed price, peak rule or limit always stops. After the grace
    period the old rule applies: any unreadable or changed source stops. A failed check is never cached."""
    now=datetime.datetime.now(datetime.timezone.utc)
    checked=ProviderSnapshot(snapshot);checked.fresh(now)
    sources={s['url']:s['sha256'] for s in snapshot['sources']}
    if set(sources)!={PRICING_URL,API_URL}:raise ValueError('fixed vendor source set changed; admission stopped')
    log=log or (lambda text:print(text,flush=True))
    key=checked.id;last=_GOOD.get(key)
    if cached and last and clock()-last[0]<check_max_age_minutes()*60:return True
    if cached and key in _WARNED and clock()-_WARNED[key]<check_max_age_minutes()*60:return True
    _GOOD.pop(key,None)
    ages=[(now-checked.fetched).total_seconds()/60]
    if last:ages.append((clock()-last[0])/60)
    grace=min(ages)<GRACE_MINUTES
    last_text='last good check '+last[1] if last else 'no earlier good check in this process'
    def warn(kind,why):
        _WARNED[key]=clock()
        _note(log,kind,'vendor check: '+why+'; last good check %.0f minutes ago (grace %d), so the run continues; '%(min(ages),GRACE_MINUTES)+last_text)
        return True
    try:bodies={url:fetch_verified_source(url,fetcher) for url in sources}
    except Exception as exc:
        if not grace:raise
        return warn('vendor-read',type(exc).__name__+': '+str(exc)[:200].replace('\n',' '))
    if any(hashlib.sha256(bodies[u].encode()).hexdigest()!=d for u,d in sources.items()):
        try:current=parse(bodies[PRICING_URL],bodies[API_URL],snapshot['model'])
        except ValueError as exc:
            if grace and not any(w in str(exc) for w in HARD_PARSE_WORDS):return warn('vendor-read','the published model table no longer parses ('+str(exc)[:200]+')')
            raise ValueError('vendor source changed during run and the published model table no longer parses ('+str(exc)+'); preserve accepted attempts and review new snapshot; '+last_text) from None
        changed=[k for k in FACT_KEYS if current.get(k)!=snapshot.get(k)]
        if current['context_proposal']['maximum_output_tokens']!=snapshot['context_proposal']['maximum_output_tokens']:changed.append('maximum_output_tokens')
        hard=[k for k in changed if k not in WARN_ONLY_FACTS]
        if hard or (changed and not grace):raise ValueError('vendor source changed during run: pinned terms differ ('+', '.join(changed)+'); preserve accepted attempts and review new snapshot; '+last_text)
        if changed:
            return warn('vendor-change','pinned '+', '.join('%s %r -> %r'%(k,snapshot.get(k),current.get(k)) for k in changed)+'; prices, peak rule and limits unchanged')
        log(datetime.datetime.now(datetime.timezone.utc).isoformat()+' vendor pages changed but every pinned term is identical; check passes')
    _GOOD[key]=(clock(),datetime.datetime.now(datetime.timezone.utc).isoformat())
    return True
