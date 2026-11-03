"""Separate private operations listener. All authority stays in service APIs.

No startup recovery, direct DB access, credit edits or global operator grant.
"""
import os
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles

ROUTES={'access':'/api/operator/backoffice-access','audit-events':'/api/operator/audit-events','active-jobs':'/api/operations/active-jobs','payment-journal':'/api/operator/payment-journal','payment-totals':'/api/operator/payment-totals','workspace':'/api/admin/operations/workspace','orphaned-tasks':'/api/admin/operations/orphaned-tasks',
    'operator-sessions':'/api/operator/sessions','operator-credits':'/api/operator/credits',
    'expense-totals':'/api/operator/expense-totals','vendor-status':'/api/operator/vendor-status','task-debug':'/api/operator/task-debug','task-logs':'/api/operator/task-logs','credit-account':'/api/operator/credit-account','task-usage':'/api/operator/task-usage',
    'attempt-releases':'/api/admin/operations/attempt-releases','model-pins':'/api/operator/model-pins','acceptance-readiness':'acceptance-readiness'}
QUERY_KEYS={'audit-events':{'before','limit','event'},'active-jobs':{'limit','before_updated_at','before_job_id'},'payment-journal':{'start_at','end_at','owner_id','limit','before_created_at','before_purchase_id'},'payment-totals':{'start_at','end_at','owner_id','limit'},'task-usage':{'task_id'},'credit-account':{'owner_id'},'task-logs':{'task_id','before_id','limit'},'task-debug':{'task_id'},'attempt-releases':{'limit'},'operator-sessions':{'limit','before_created_at','before_session_id'},
            'operator-credits':{'limit','after_owner'}}


def settings(environ):
    from .networking import delivery_listener
    delivery_host,delivery_port=delivery_listener(environ)
    host=environ.get('BACKOFFICE_HOST','127.0.0.1');value=environ.get('BACKOFFICE_PORT','8020')
    if host not in ('127.0.0.1','::1','localhost'):raise ValueError('back office is private until exposure/auth design is reviewed')
    if not isinstance(value,str) or not value.isascii() or not value.isdigit() or not 1<=int(value)<=65535:
        raise ValueError('BACKOFFICE_PORT must be an integer from 1 to 65535')
    port=int(value)
    engine_port=int(environ.get('INTERVIEW_ENGINE_PORT','8010'))
    if port in (delivery_port,engine_port):raise ValueError('back-office listener conflicts with delivery or engine port')
    base=environ.get('BACKOFFICE_SERVICE_URL',f'http://127.0.0.1:{delivery_port}')
    parsed=urlsplit(base)
    if (parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1')
        or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/')):
        raise ValueError('explicit same-host service API origin required')
    return host,port,base.rstrip('/')


def create_app(*,service_url,transport=None):
    application=FastAPI(title='Requi.re back office',docs_url=None,redoc_url=None,openapi_url=None)
    from pathlib import Path
    application.mount('/assets',StaticFiles(directory=Path(__file__).parent/'web'),name='shared-assets')
    application.mount('/backoffice-assets',StaticFiles(directory=Path(__file__).parent/'backoffice_web'),name='backoffice-assets')
    @application.get('/favicon.ico')
    def icon():
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).parent/'web'/'favicon.ico')
    @application.get('/',response_class=HTMLResponse)
    def shell():
        from pathlib import Path
        return HTMLResponse((Path(__file__).parent/'backoffice_web'/'index.html').read_text(),
            headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",'Referrer-Policy':'no-referrer'})
    @application.get('/health')
    def health():return {'ok':True,'role':'backoffice','capabilities':'scoped_service_api_proxy'}
    @application.get('/api/operations/{capability}')
    async def read_operation(capability:str,request:Request):
        target=ROUTES.get(capability)
        if target is None:return JSONResponse({'detail':'Unknown operations capability'},status_code=404)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Cross-site operations read denied'},status_code=403)
        query=list(request.query_params.multi_items())
        allowed=QUERY_KEYS.get(capability,set())
        if (any(key not in allowed or len(value)>240 for key,value in query)
            or len({key for key,value in query})!=len(query)):
            return JSONResponse({'detail':'Unsupported operations page parameters'},status_code=422)
        # No operator token from environment, storage or query parameters. The
        # caller's identity is rechecked by delivery on every data request.
        headers={key:request.headers[key] for key in ('authorization','cookie') if key in request.headers}
        if not headers:return JSONResponse({'detail':'Service API sign-in required'},status_code=401)
        try:
            async with httpx.AsyncClient(base_url=service_url,transport=transport,timeout=10,follow_redirects=False) as client:
                if capability=='acceptance-readiness':
                    from .acceptance_readiness import inspect
                    paths=('/api/auth','/api/operations/test-tasks','/api/operator/model-pins')
                    values={}
                    for path in paths:
                        response=await client.get(path,headers=headers)
                        if path=='/api/operator/model-pins' and response.status_code!=200:
                            return JSONResponse({'detail':'Reviewed model-pin access is required for acceptance readiness'},status_code=403,headers={'Cache-Control':'no-store'})
                        if response.status_code==200 and len(response.content)<=1024*1024:values[path]=response.json()
                    def read(method,path):
                        if path not in values:raise ValueError('Readiness evidence unavailable')
                        return values[path]
                    result=inspect(read)
                    return JSONResponse({'checks':result['checks'],'ready':result['ready'],
                        'activation_enabled':False,'owner_only':True},headers={'Cache-Control':'no-store'})
                async with client.stream('GET',target,headers=headers,params=query) as response:
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>1024*1024:raise ValueError('operations response exceeds bound')
                    if response.status_code not in (200,401,403,404,409,422,503):
                        return JSONResponse({'detail':'Operations service returned an unsupported response'},status_code=502)
                    import json
                    value=json.loads(body)
                    if not isinstance(value,dict):raise ValueError('operations object required')
                    return JSONResponse(value,status_code=response.status_code,headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,ValueError):
            return JSONResponse({'detail':'Operations service unavailable or invalid'},status_code=502)
    @application.post('/api/operations/worker-jobs/{job_id}/stop')
    async def stop_job(job_id:str,request:Request):
        import re,json
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,240}',job_id):return JSONResponse({'detail':'Invalid job identifier'},status_code=422)
        # This listener has one narrowly routed write, never a generic proxy.
        origin=request.headers.get('origin')
        if origin!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Same-origin confirmation required'},status_code=403)
        raw=await request.body()
        if len(raw)>256:return JSONResponse({'detail':'Bounded confirmation required'},status_code=422)
        try:
            value=json.loads(raw)
            if value not in ({'confirm':True},{'confirm':True,'hide':False},{'confirm':True,'hide':True}):raise ValueError()
        except (ValueError,TypeError):return JSONResponse({'detail':'Explicit confirmation required'},status_code=422)
        headers={key:request.headers[key] for key in ('authorization','cookie') if key in request.headers}
        if not headers:return JSONResponse({'detail':'Service API sign-in required'},status_code=401)
        # Forward the listener's reviewed upstream origin only after verifying
        # the actual browser origin above. Service still checks identity/registry.
        headers['Origin']=service_url;headers['Content-Type']='application/json'
        try:
            async with httpx.AsyncClient(base_url=service_url,transport=transport,timeout=20,follow_redirects=False) as client:
                response=await client.post('/api/operations/worker-jobs/'+job_id+'/stop',headers=headers,json=value)
                if len(response.content)>65536 or response.status_code not in (200,401,403,404,409,422,503):raise ValueError()
                value=response.json()
                if not isinstance(value,dict):raise ValueError()
                return JSONResponse(value,status_code=response.status_code,headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,ValueError):return JSONResponse({'detail':'Stop outcome unverified. Refresh jobs; do not infer accounting settlement.'},status_code=502)
    @application.post('/api/operations/audit-events/{audit_id}/resolve')
    async def resolve_audit(audit_id:int,request:Request):
        import json
        if request.headers.get('origin')!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'detail':'Same-origin confirmation required'},status_code=403)
        raw=await request.body()
        if len(raw)>512:return JSONResponse({'detail':'Bounded confirmation required'},status_code=422)
        try:
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)-{'action','confirm','expected_attempt_state'} or value.get('confirm') is not True or value.get('action') not in ('hide','cancel','release'):raise ValueError()
        except (ValueError,TypeError):return JSONResponse({'detail':'Explicit bounded confirmation required'},status_code=422)
        headers={k:request.headers[k] for k in ('authorization','cookie') if k in request.headers}
        if not headers:return JSONResponse({'detail':'Sign in required'},status_code=401)
        headers.update(Origin=service_url,**{'Content-Type':'application/json'})
        try:
            async with httpx.AsyncClient(base_url=service_url,transport=transport,timeout=20,follow_redirects=False) as client:
                response=await client.post('/api/operations/audit-events/'+str(audit_id)+'/resolve',headers=headers,json=value)
                if len(response.content)>65536 or response.status_code not in (200,401,403,404,409,422,503):raise ValueError()
                return JSONResponse(response.json(),status_code=response.status_code,headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,ValueError):return JSONResponse({'detail':'Resolution unverified. Refresh audit before doing anything else.'},status_code=502)
    @application.post('/api/operations/model-pins/{action}')
    async def model_pin_action(action:str,request:Request):
        if action not in ('new','new-local','refresh','edit','approve','expense-policy'):return JSONResponse({'detail':'Unknown pin action'},status_code=404)
        if request.headers.get('origin')!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'detail':'Same-origin confirmation required'},status_code=403)
        raw=await request.body()
        if len(raw)>65536:return JSONResponse({'detail':'Pin review exceeds bound'},status_code=422)
        headers={k:request.headers[k] for k in ('authorization','cookie') if k in request.headers}
        if not headers:return JSONResponse({'detail':'Service API sign-in required'},status_code=401)
        headers.update(Origin=service_url,**{'Content-Type':'application/json'})
        try:
            async with httpx.AsyncClient(base_url=service_url,transport=transport,timeout=30,follow_redirects=False) as client:
                response=await client.post('/api/operator/model-pins/'+action,headers=headers,content=raw)
                if len(response.content)>1024*1024 or response.status_code not in (200,401,403,404,409,422,503):raise ValueError()
                return JSONResponse(response.json(),status_code=response.status_code,headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,ValueError):return JSONResponse({'detail':'Pin outcome unverified. Refresh pins and audit history; Back office > Debug > Download evidence.'},status_code=502)
    return application


def main():
    import uvicorn
    host,port,base=settings(os.environ)
    print(f'Private back office: http://{host}:{port}; service APIs: {base}')
    uvicorn.run(create_app(service_url=base),host=host,port=port)


if __name__=='__main__':main()
