"""Generated snapshot compiler. No founder copies vendor price/band fields."""
import datetime,hashlib,json
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
    if any(unresolved(data.get(k)) for k in BASE):raise ValueError('required local sandbox bindings, keys and fixture model missing')
    rates=RetailRates.from_environment({k:data[k] for k in RETAIL if k in data})
    snapshot,checked=load_snapshot(root,data['MODEL']);ceiling=checked.ceiling()
    price={'provider':'deepseek','model':data['MODEL'],'source':PRICING_URL,'price_version':'vendor-'+checked.id,'expense_input':ceiling['cache_miss'],'expense_cache_hit':ceiling['cache_hit'],'expense_cache_miss':ceiling['cache_miss'],'expense_output':ceiling['output'],'expense_units_per_usd':10**12,'retail_units_per_credit':UNITS_PER_CREDIT,'retail_input':int(rates.price_per_token*UNITS_PER_CREDIT),'retail_output':int(rates.price_per_token*UNITS_PER_CREDIT),'retail_formula':rates.snapshot(),'provider_snapshot':snapshot,'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}
    context={'model':data['MODEL'],'context_tokens':128000,'maximum_output_tokens':min(128000,snapshot['context_proposal']['maximum_output_tokens']),'source':snapshot['protective_ceiling_source'],'bound_version':'owner-run-128000-v1','protective_run_ceiling':True}
    PayPalBinding(data['SANDBOX_MERCHANT_ID'],data['SANDBOX_WEBHOOK_ID'],False)
    if any(not isinstance(data[k],str) or not data[k] for k in SECRET):raise ValueError('local credential fields required')
    run={'root':'/evidence/run','database_name':identity['database_name'],'database_host':'127.0.0.1','service_file':'/private/services.conf','admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment','model':data['MODEL'],'catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json','verified_sandbox_merchant':data['SANDBOX_MERCHANT_ID'],'sandbox_webhook_id':data['SANDBOX_WEBHOOK_ID'],'operator_registry_version':'item6-local-'+identity['run_id'][:12]}
    return {'catalog.json':[price],'context.json':context,'run.json':run,'credentials.json':{'client_id':data['PAYPAL_CLIENT_ID'],'client_secret':data['PAYPAL_CLIENT_SECRET'],'deepseek_api_key':data['DEEPSEEK_API_KEY']}}
