"""Combine separately reviewed single-provider pins for the explicit both preview.

No evaluation of environment-file contents. No network, key read, or price default.
"""
import json,os,sys
from pathlib import Path

def combine(output,inputs):
    rows=[];contexts=[];providers=set()
    for directory in inputs:
        values={}
        for line in (Path(directory)/'cloud-models.env').read_text().splitlines():
            if not line or line.startswith('#'):continue
            key,separator,value=line.partition('=')
            if not separator or key in values or key not in ('VMODEL_REVIEWED_MODEL_PRICES','VMODEL_REVIEWED_CONTEXT_CEILING'):
                raise ValueError('unexpected or duplicate pin field')
            values[key]=value
        prices=json.loads(values['VMODEL_REVIEWED_MODEL_PRICES']);context=json.loads(values['VMODEL_REVIEWED_CONTEXT_CEILING'])
        if not isinstance(prices,list) or len(prices)!=1 or not isinstance(prices[0],dict) or not isinstance(context,dict):raise ValueError('single-provider pin required')
        provider=prices[0].get('provider')
        if provider not in ('deepseek','ollama') or provider in providers:raise ValueError('one pin per supported provider required')
        if context.get('model')!=prices[0].get('model') or 'provider' in context:raise ValueError('pin model/context mismatch')
        providers.add(provider);rows.extend(prices);contexts.append(dict(context,provider=provider))
    if providers!={'deepseek','ollama'}:raise ValueError('both independently reviewed providers required')
    from delivery_api.production_gateway import ProductionGateways
    env={'VMODEL_REVIEWED_MODEL_PRICES':json.dumps(rows,separators=(',',':')),
         'VMODEL_REVIEWED_CONTEXT_CEILING':json.dumps(contexts,separators=(',',':'))}
    # Offline same validation, placeholder never written or sent to a transport.
    factory=ProductionGateways(None,{**env,'DEEPSEEK_API_KEY':'validation-only-not-written'})
    factory.close()
    target=Path(output)/'cloud-models.env'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write('# Independently reviewed DeepSeek and local Ollama pins. No credentials.\n')
        for key,value in env.items():stream.write(key+'='+value+'\n')

if __name__=='__main__':
    try:combine(sys.argv[1],sys.argv[2:])
    except Exception as exc:raise SystemExit('PIN-FAIL dual configuration: '+str(exc))
    print('BOTH       independently reviewed DeepSeek + zero-cost local Ollama; choose in the UI, no fallback')
