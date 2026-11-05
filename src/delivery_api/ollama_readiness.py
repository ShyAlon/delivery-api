"""Bounded read-only Ollama checks. No prompts, pulls or model loads."""
import json,subprocess,sys,os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kw):return None

def probe(base,model=None):
    out={'reachable':False,'installed_models':[],'selected_model_present':False,'memory_fit':'unverified'}
    try:
        url=urlsplit(base)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path.rstrip('/') not in ('','/v1'):
            return {**out,'reason':'endpoint_invalid'}
        port=url.port
        origin=base.rstrip('/')[:-3] if base.rstrip('/').endswith('/v1') else base.rstrip('/')
        opener=build_opener(ProxyHandler({}),NoRedirect())
        def get(path):
            with opener.open(Request(origin+path,method='GET'),timeout=3) as response:
                raw=response.read(262145)
                if len(raw)>262144:raise ValueError('too large')
                return json.loads(raw)
        tags=get('/api/tags');rows=tags.get('models')
        if not isinstance(rows,list):raise ValueError('models')
        models=[]
        for row in rows:
            name=row.get('name') if isinstance(row,dict) else None
            if not isinstance(name,str) or not __import__('re').fullmatch(r'[a-zA-Z0-9._:/-]{1,200}',name):continue
            models.append({'id':name,'file_bytes':row.get('size') if type(row.get('size')) is int else None})
        names={r['id'] for r in models}
        out.update(reachable=True,installed_models=models,selected_model_present=bool(model and (model in names or ':' not in model and model+':latest' in names)))
        try:
            loaded=get('/api/ps').get('models',[])
            out['loaded_models']=[{'id':r['name'],'resident_bytes':r.get('size') if type(r.get('size')) is int else None,'vram_bytes':r.get('size_vram') if type(r.get('size_vram')) is int else None,'context_length':r.get('context_length') if type(r.get('context_length')) is int else None} for r in loaded if isinstance(r,dict) and r.get('name') in names]
        except Exception:out['loaded_models_available']=False
        out['reason']='model_not_selected' if not model else 'ready_for_start_check' if out['selected_model_present'] else 'selected_model_missing'
        return out
    except Exception:return {**out,'reason':'ollama_unreachable_or_invalid_listing'}

