"""Bounded OpenAI envelope adapter to native Ollama with enforced context.

The journal retains exact caller bytes. Only the transport envelope changes;
no messages, outputs, model identity or cap may be silently dropped or clamped.
"""
import json
import httpx
from .provider_http import ProviderHTTPError,MAX_RESPONSE,provider_timeout


def prepare(raw,context):
    value=json.loads(raw);model=value.get('model')
    if not isinstance(context,dict) or context.get('model')!=model:raise ValueError('Reviewed local model context is required')
    if value.get('think',False) is not False:raise ValueError('This execution plan requires non-thinking output')
    # Older SDK defaults cannot override the immutable registry execution plan.
    allowed={'model','messages','stream','stream_options','max_tokens','max_completion_tokens','temperature','top_p','seed','stop','response_format','num_ctx','think','keep_alive'}
    if set(value)-allowed:raise ValueError('Unsupported local request fields: '+', '.join(sorted(set(value)-allowed)))
    cap=value.get('max_tokens',value.get('max_completion_tokens'))
    if type(cap) is not int or not 0<cap<=context['maximum_output_tokens']:raise ValueError('Local output cap exceeds capability')
    # Conservative admission guard, never claim a tokenizer proof. The
    # request uses the reviewed window instead of a silent server default.
    if len(raw)+4096+cap>context['context_tokens']:raise ValueError('Local request may exceed context; split this phase before retrying')
    options={'num_ctx':context['context_tokens'],'num_predict':cap}
    for key in ('temperature','top_p','seed','stop'):
        if key in value:options[key]=value[key]
    payload={'model':model,'messages':value['messages'],'stream':False,'think':False,'options':options}
    if 'keep_alive' in value:payload['keep_alive']=value['keep_alive']
    fmt=value.get('response_format')
    if fmt:
        if fmt.get('type')=='json_schema':payload['format']=fmt['json_schema']['schema']
        elif fmt.get('type')=='json_object':payload['format']='json'
        else:raise ValueError('Unsupported local structured output format')
    return payload


def send(client,endpoint,raw,context):
    value=json.loads(raw);model=value['model'];payload=prepare(raw,context)
    url=str(httpx.URL(endpoint).copy_with(path='/api/chat'))
    request=client.build_request('POST',url,json=payload,timeout=provider_timeout())
    response=client.send(request,stream=True,follow_redirects=False)
    try:
        body=bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body)>MAX_RESPONSE:raise ProviderHTTPError('Local response exceeded the supported size')
        if response.status_code!=200:raise ProviderHTTPError('Ollama returned HTTP '+str(response.status_code)+'. Review this attempt before retrying.',status=response.status_code)
        result=json.loads(body)
        if result.get('done') is not True or not isinstance(result.get('message'),dict):raise ProviderHTTPError('Local response is incomplete')
        usage={'prompt_tokens':result.get('prompt_eval_count'),'completion_tokens':result.get('eval_count')}
        if any(type(v) is not int or v<0 for v in usage.values()):raise ProviderHTTPError('Local response has no terminal usage')
        usage['total_tokens']=sum(usage.values())
        if result.get('model') not in (None,model):raise ProviderHTTPError('Local response names a different model')
        if result.get('done_reason') not in ('length','stop'):raise ProviderHTTPError('Local response has no supported completion reason')
        reason='length' if result['done_reason']=='length' else 'stop'
        reply={'id':'ollama-native','object':'chat.completion','model':model,'choices':[{'index':0,'message':result['message'],'finish_reason':reason}],'usage':usage}
        if not value.get('stream'):return json.dumps(reply).encode(),'application/json'
        content={'id':reply['id'],'object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':result['message'],'finish_reason':None}]}
        terminal={'id':reply['id'],'object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':{},'finish_reason':reason}],'usage':usage}
        return b'data: '+json.dumps(content).encode()+b'\n\ndata: '+json.dumps(terminal).encode()+b'\n\ndata: [DONE]\n\n','text/event-stream'
    finally:response.close()
