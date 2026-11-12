"""Canonical selectable model capabilities. Discovery never starts a model or spends.

Local capabilities are public, versioned product data, not operator money
approval. The selected payload is retained in the existing per-generation pin.
Paid models still require an approved durable pin and a parent-held key.
"""
from .ollama_readiness import probe

QWEN_SOURCE='https://huggingface.co/Qwen/Qwen3-8B'
QWEN_PACKAGE='https://ollama.com/library/qwen3:8b'
# Official Ollama Q4_K_M package, checked 2026-11-12. A changed package needs
# capability review, not automatic inheritance of a familiar tag's limits.
QWEN_DIGEST='500a1f067a9f'
QWEN_CONTEXT=32768
QWEN_OUTPUT=12288


def ollama_base(environ):
    if environ.get('OLLAMA_BASE_URL'):return environ['OLLAMA_BASE_URL'].rstrip('/')
    owned='host=/owned/socket' in environ.get('DELIVERY_DATABASE_URL','')
    return 'http://host.docker.internal:11434/v1' if owned else 'http://localhost:11434/v1'


LOCAL_MODELS={
    'qwen3:8b':{'digests':(QWEN_DIGEST,), 'source':QWEN_SOURCE,'package':QWEN_PACKAGE,'native_context':32768,'runtime_context':32768},
    'qwen3.5:9b':{'digests':('56671c2ab938','d63f830f28d5'),
        'source':'https://huggingface.co/Qwen/Qwen3.5-9B','package':'https://ollama.com/library/qwen3.5:9b','native_context':262144,'runtime_context':131072},
}


def local_deployment(environ):
    """Publicly hosted deployments never offer the user's computer as a route.

    Owned local PG is launched with a loopback public origin. Do not use an
    endpoint's reachability as evidence that the deployment should expose it.
    """
    from urllib.parse import urlsplit
    origin=environ.get('DELIVERY_PUBLIC_ORIGIN','')
    if not origin:return True # legacy process/test configuration, no remote profile
    try:return urlsplit(origin).hostname in ('localhost','127.0.0.1','::1')
    except ValueError:return False


def native_model(model):return model in LOCAL_MODELS


def local_capability(model,digest):
    import re
    record=LOCAL_MODELS.get(model)
    if not record or not isinstance(digest,str) or not any(re.fullmatch(prefix+'[0-9a-f]{52}',digest.removeprefix('sha256:')) for prefix in record['digests']):
        raise ValueError('Local model package has no verified capability record')
    # Application envelope, deliberately below the model-card output examples
    # (32768 for both entries). Not a claim about all possible hardware fits.
    version=model+'-ollama-2026-11-12-'+digest.removeprefix('sha256:')
    return {'row':{'provider':'ollama','model':model,'price_version':version,'source':record['package'],
        'expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9},
        'context':{'model':model,'context_tokens':record['runtime_context'],'maximum_output_tokens':QWEN_OUTPUT,
        'source':record['source'],'bound_version':version,'protective_run_ceiling':False}}


def phase_budgets():
    from spec_generator import model_config as generator
    from interview_engine import model_config as interview
    return {**{'generator/'+stage:generator._max_tokens_limit(value.max_tokens) for stage,value in generator._STAGE_DEFAULTS.items()},
        **{'interview/'+stage:interview._max_tokens_limit(value.max_tokens) for stage,value in interview._STAGE_DEFAULTS.items()}}


def unsupported(context,budgets):
    cap=min(context['context_tokens'],context['maximum_output_tokens'])
    return [f'{stage} needs {need} output tokens; this model allows {cap}.' for stage,need in budgets.items() if need>cap]


def discover(heads,environ,listing=None):
    """Only known installed packages or approved paid heads. No env model choice."""
    from .model_pins import validate
    records=[]
    for head in heads:
        if head['provider'] not in ('deepseek','ollama'):continue
        if head['provider']=='ollama' and not local_deployment(environ):continue
        if head['provider']=='deepseek' and not environ.get('DEEPSEEK_API_KEY','').strip():continue
        try:payload=validate(head['payload'])
        except (ValueError,KeyError,TypeError):continue
        model=payload['row']['model']
        if head.get('model',model)!=model or payload['row']['provider']!=head['provider']:continue
        records.append({'id':head['provider']+':'+model,'provider':head['provider'],'model':model,
            'payload':payload,'is_default':head.get('is_default',False),'source':'approved-pin'})
    if listing is None:listing=probe(ollama_base(environ)) if local_deployment(environ) else {'reachable':False}
    ready_native=set()
    if listing.get('reachable'):
        for model in listing.get('installed_models',[]):
            try:
                payload=validate(local_capability(model['id'],model.get('digest')))
                caps=model.get('capabilities',[])
                controls=model.get('thinking') or {}
                if 'completion' not in caps or not any(type(v) is bool and v is False for v in controls.get('values',[])):continue
                if type(model.get('native_context')) is not int or model['native_context']<payload['context']['context_tokens']:continue
            except (ValueError,KeyError,TypeError):continue
            ready_native.add(model['id'])
            # Explicit operator review remains authoritative when present.
            if any((r['provider'],r['model'])==('ollama',model['id']) for r in records):continue
            records.append({'id':'ollama:'+model['id'],'provider':'ollama','model':model['id'],
                'payload':payload,'is_default':True,'source':'verified-local-package'})
    # One runtime bootstrap default per provider; exact UI selection can name
    # any ready entry. Prefer the newly selected family, never a hidden fallback.
    records=[r for r in records if r['provider']!='ollama' or not native_model(r['model']) or r['model'] in ready_native]
    local=[r for r in records if r['provider']=='ollama']
    local.sort(key=lambda r:(r['model']!='qwen3.5:9b',r['model']))
    for index,record in enumerate(local):record['is_default']=index==0
    return [r for r in records if r['provider']!='ollama']+local


def resolve(records,provider,model):
    found=next((r for r in records if r['provider']==provider and r['model']==model),None)
    if found is None:raise ValueError('Selected model is not ready. Reload the model list; no other model was selected.')
    failures=unsupported(found['payload']['context'],phase_budgets())
    if failures:raise ValueError('This model cannot run the full cycle: '+' '.join(failures))
    return found
