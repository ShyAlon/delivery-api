"""Grounds the local Ollama reviewed $0 price row and context ceiling from the actual server on this Mac.

Local HTTP only: server version, model card, and the loaded context window the server itself reports
(/api/ps). Loading the model is a local $0 operation, never a paid or vendor call. The output bound is
the largest stage output cap read from the engine and generator checkouts at their candidate revisions,
so no engine-only cap silently stands for a generator stage. Fails closed with a named fix.
"""
import datetime,json,os,re,sys,urllib.request

def die(why):
    print('PIN-FAIL '+why);sys.exit(2)

out=sys.argv[1]
root=sys.argv[2]
host=os.environ.get('OLLAMA_HOST','http://localhost:11434').rstrip('/')
model=os.environ.get('PREVIEW_EXEC_OLLAMA_MODEL','').strip() or 'phi4'
if '://' not in host: die('OLLAMA_HOST must be a plain http(s) URL')

def call(method,path,payload=None,timeout=30):
    req=urllib.request.Request(host+path,data=json.dumps(payload).encode() if payload is not None else None,
        method=method,headers={'content-type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read())

try:version=call('GET','/api/version')
except Exception as exc:die('cannot reach the Ollama server at %s: %s: %s - start it (ollama serve)'%(host,type(exc).__name__,str(exc)[:200]))
try:call('POST','/api/show',{'model':model})
except Exception as exc:die('the model %s is not on this server: %s: %s - pull it first: ollama pull %s'%(model,type(exc).__name__,str(exc)[:200],model))
# Inspect an already-loaded model without changing its lifetime. It may belong
# to another active run; never unload someone else's warm model.
try:before=call('GET','/api/ps')
except Exception as exc:die('cannot inspect model ownership before grounding: '+type(exc).__name__)
def matching(ps):
    return [m for m in ps.get('models',[]) if str(m.get('model','')).split(':')[0]==model.split(':')[0]]
owned=not matching(before)
try:
    if owned:
        call('POST','/api/generate',{'model':model,'prompt':'','keep_alive':'1m','stream':False},timeout=600)
    ps=call('GET','/api/ps')
    loaded=matching(ps)
    if not loaded or not isinstance(loaded[0].get('context_length'),int) or loaded[0]['context_length']<=0:
        die('the server did not report the loaded context window for %s'%model)
    server_ctx=loaded[0]['context_length']
except Exception as exc:die('model grounding failed: '+type(exc).__name__)
finally:
    if owned:
        try:
            call('POST','/api/generate',{'model':model,'prompt':'','keep_alive':0,'stream':False},timeout=60)
            print('GROUNDING  temporary model unload requested (keep_alive=0)')
        except Exception as exc:
            die('temporary model unload failed: '+type(exc).__name__+'; run ollama stop '+model+' before retrying')
    else:
        print('GROUNDING  model was already loaded; its existing lifetime was not changed')
try:requested=int(os.environ.get('VMODEL_OLLAMA_NUM_CTX') or '32768')
except ValueError:die('VMODEL_OLLAMA_NUM_CTX is not a number')
if requested<=0:die('VMODEL_OLLAMA_NUM_CTX must be positive')
# The effective window is the smaller of what the server loaded and what the engines ask for per call.
context=min(server_ctx,requested)

# Largest reviewed output cap across engine and generator stage defaults at the candidate revisions.
caps=[]
for rel in ('interview-engine/src/interview_engine/model_config.py','spec-generator/src/spec_generator/model_config.py'):
    path=os.path.join(root,rel)
    try:text=open(path).read()
    except OSError:die('cannot read %s - is the checkout at %s present?'%(rel,root))
    block=text.split('_STAGE_DEFAULTS')[1].split('}')[0]
    for entry in re.findall(r'StageModel\([^)]*\)',block):
        m=re.search(r'max_tokens=(\d+)',entry)
        caps.append(int(m.group(1)) if m else 2048)
if not caps:die('no stage defaults found in the engine/generator checkouts')
max_out=max(caps)
if context<=max_out:
    die('the effective context window %d cannot hold the largest stage output %d plus a prompt - raise OLLAMA_CONTEXT_LENGTH on the server (and VMODEL_OLLAMA_NUM_CTX here) or use a model with a larger context'%(context,max_out))

now=datetime.datetime.now(datetime.timezone.utc)
version_tag='local-ollama-'+now.strftime('%Y%m%d')
source='local server %s (%s) ; docs.ollama.com/context-length ; stage caps from candidate checkouts'%(host,version.get('version','?'))
row={'provider':'ollama','model':model,'price_version':version_tag,'source':source,
    'expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9}
ceiling={'model':model,'context_tokens':context,'maximum_output_tokens':max_out,'source':source,'bound_version':version_tag,'protective_run_ceiling':False}
env={'VMODEL_REVIEWED_MODEL_PRICES':json.dumps([row],separators=(',',':'),sort_keys=True),
     'VMODEL_REVIEWED_CONTEXT_CEILING':json.dumps(ceiling,separators=(',',':'),sort_keys=True)}
# The same checks the server runs, offline against the settings just written.
try:
    from delivery_api import production_gateway as pg
    check=dict(env)
    factory=pg.gateways_from_environment(None,check);factory.close()
except Exception as exc:die('the server would refuse these settings: %s: %s'%(type(exc).__name__,str(exc)[:300]))
text='# Reviewed local Ollama $0 row and context ceiling, read %s. Generated by the local pin script; do not edit.\n%s=%s\n%s=%s\n'%(now.isoformat(),'VMODEL_REVIEWED_MODEL_PRICES',env['VMODEL_REVIEWED_MODEL_PRICES'],'VMODEL_REVIEWED_CONTEXT_CEILING',env['VMODEL_REVIEWED_CONTEXT_CEILING'])
open(os.path.join(out,'cloud-models.env'),'w').write(text)
print('MODEL      %s (server Ollama %s)'%(model,version.get('version','?')))
print('SERVER CTX %d tokens (the window the server actually loaded)'%server_ctx)
print('REQUESTED  %d tokens per call (VMODEL_OLLAMA_NUM_CTX)'%requested)
print('CEILING    %d tokens per call, max output %d (largest stage cap across engine+generator)'%(context,max_out))
print('COST       $0 - local model, every call still reserved and settled in the shared ledger')
print('READ AT    '+now.isoformat())
