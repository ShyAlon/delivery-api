"""Dedicated normal browser; strict hosted verification before local enrollment.

Only this controller receives a transient access token from its own browser.
No token/password/refresh cookie is persisted or printed. The accepted app
still handles sign-in, cookies, refresh and every authenticated API normally.
"""
import json
import time
import httpx
from image_workflow import WorkflowError,log


def verified_identity(token,project,subject,*,anon_key,verifier=None,client=None):
    from delivery_api.supabase_auth import SupabaseIdentityVerifier
    verifier=verifier or SupabaseIdentityVerifier(project)
    scope,_,email=verifier.verify_with_email(token)
    if scope.owner_id!=subject or not email:raise WorkflowError('Sign in as the reviewed local operator; no other subject is enrolled')
    owned=client is None
    client=client or httpx.Client(timeout=10,trust_env=False,follow_redirects=False)
    try:
        # Hosted live /user check complements JWT verification. Does NOT prove
        # auth.sessions revocation parity; some revocations remain valid to exp.
        response=client.get(project+'/auth/v1/user',headers={'Authorization':'Bearer '+token,'apikey':anon_key})
        if response.status_code!=200 or len(response.content)>65536:raise WorkflowError('Hosted identity check unavailable; local session not enrolled')
        user=response.json()
        if user.get('id')!=scope.owner_id:raise WorkflowError('Hosted identity differs; local session not enrolled')
        import jwt
        claims=jwt.decode(token,options={'verify_signature':False})  # already strictly verified above
        return {'sub':scope.owner_id,'session_id':scope.session_id,'email':email,'exp':claims['exp'],'iat':claims['iat']}
    finally:
        if owned:client.close()


def browse(base,values,controller,*,backoffice=None,headless=False,on_open=None):
    from playwright.sync_api import sync_playwright
    project=values['DELIVERY_SUPABASE_URL'];subject=values['REQUI_OPERATOR_SUBJECT']
    origins=[base]+([backoffice] if backoffice else [])
    current={};last_token=[None];last_check=[0.0]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=headless)
        try:
            context=browser.new_context()
            page=context.new_page()
            def revoke():
                if current:
                    controller({'action':'revoke','session_id':current['session_id']});current.clear()
                    last_token[0]=None
            def auth_request(route):
                path=route.request.url.split('?',1)[0]
                matched=next((origin for origin in origins if path.startswith(origin+'/auth/')),None)
                if not matched:raise WorkflowError('unreviewed sign-in origin')
                response=route.fetch()
                try:
                    if path==matched+'/auth/logout':revoke()
                    elif response.status==200 and path in (matched+'/auth/signin',matched+'/auth/refresh'):
                        # Enroll before releasing the real sign-in response so
                        # the next app read cannot race missing local auth rows.
                        from http.cookies import SimpleCookie
                        found=[]
                        for header in response.headers_array:
                            if header['name'].lower()=='set-cookie':
                                cookies=SimpleCookie();cookies.load(header['value'])
                                if 'requi_access' in cookies:found.append(cookies['requi_access'].value)
                        if len(found)!=1:raise WorkflowError('Hosted sign-in access credential missing; no local enrollment')
                        token=found[0]
                        item=verified_identity(token,project,subject,anon_key=values['DELIVERY_SUPABASE_ANON_KEY'])
                        if current and current['session_id']!=item['session_id']:revoke()
                        controller({'action':'enroll','identity':item,'config':{'subject':subject,'version':values['REQUI_OPERATOR_REGISTRY_VERSION'],'capabilities':json.loads(values['REQUI_OPERATOR_CAPABILITIES'])}})
                        current.clear();current.update(item);last_token[0]=token;last_check[0]=time.monotonic()
                        log('PASS','Verified hosted session enrolled in OWNED LOCAL database; no task started. Access expires at '+time.strftime('%H:%M:%S',time.localtime(item['exp'])))
                    route.fulfill(response=response)
                except Exception:
                    revoke()
                    route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Local session enrollment failed. Inspect this terminal, then sign in again. No task started.'}))
                    raise
            for origin in origins:context.route(origin+'/auth/*',auth_request)
            page.goto(base,wait_until='domcontentloaded')
            if on_open:on_open(page)
            log('INFO','Sign in normally in this dedicated browser. Keep it and this terminal open. Refresh enrolls the verified renewed session. Closing the browser revokes local enrollment; data persists.')
            while not page.is_closed() and browser.is_connected():
                page.wait_for_timeout(500)
                if current and time.time()>=current['exp']:revoke();log('WARN','Local enrollment expired. Sign in again in this browser; no session authority is extended by the controller.')
                if current and time.monotonic()-last_check[0]>=30:
                    try:
                        item=verified_identity(last_token[0],project,subject,anon_key=values['DELIVERY_SUPABASE_ANON_KEY'])
                        if item['session_id']!=current['session_id']:raise WorkflowError('session changed')
                        last_check[0]=time.monotonic()
                    except Exception:
                        revoke();log('WARN','Hosted session check failed; local enrollment revoked. Sign in again when Auth is reachable.')
        finally:
            controller({'action':'revoke_all'})
            browser.close()
