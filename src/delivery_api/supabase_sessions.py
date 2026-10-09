"""Same-origin browser session bridge to Supabase Auth.

Access/refresh tokens stay in HttpOnly cookies, never browser storage or URLs.
Passwords pass directly to Supabase and are never persisted or logged here.
"""
import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from . import config

ACCESS_COOKIE='requi_access'
REFRESH_COOKIE='requi_refresh'


def _call(path,payload,*,access=None):
    headers={'apikey':config.SUPABASE_ANON_KEY,'Content-Type':'application/json'}
    if access:headers['Authorization']='Bearer '+access
    try:
        response=httpx.post(config.SUPABASE_URL+'/auth/v1/'+path,
                            headers=headers,json=payload,timeout=10)
        if response.status_code>=400:
            # Don't log upstream response/token payloads or leak email existence.
            raise HTTPException(401,'Sign-in failed or session expired')
        return response.json() if response.content else {}
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(503,'Authentication service unavailable') from exc


def _cookies(payload):
    access=payload.get('access_token');refresh=payload.get('refresh_token')
    ttl=payload.get('expires_in')
    if not isinstance(access,str) or not isinstance(refresh,str) or not isinstance(ttl,(float,int)) or ttl<=0:
        raise HTTPException(502,'Authentication service returned an incomplete session')
    response=JSONResponse({'status':'signed_in','expires_at':payload.get('expires_at')})
    response.headers['Cache-Control']='no-store'
    response.set_cookie(ACCESS_COOKIE,access,max_age=int(ttl),httponly=True,
                        secure=config.COOKIE_SECURE,samesite='lax',path='/')
    response.set_cookie(REFRESH_COOKIE,refresh,max_age=config.SESSION_TTL_SECONDS,
                        httponly=True,secure=config.COOKIE_SECURE,samesite='lax',path='/auth')
    return response


def signin(username,password):
    return _cookies(_call('token?grant_type=password',{'email':username,'password':password}))


def signup(username,password):
    payload=_call('signup',{'email':username,'password':password})
    if not payload.get('access_token'):
        response=JSONResponse({'status':'confirmation_required'})
        response.headers['Cache-Control']='no-store'
        return response
    return _cookies(payload)


def refresh(request):
    token=request.cookies.get(REFRESH_COOKIE)
    if not token:raise HTTPException(401,'Sign in required')
    return _cookies(_call('token?grant_type=refresh_token',{'refresh_token':token}))


def logout(request):
    token=request.cookies.get(ACCESS_COOKIE)
    if token:_call('logout?scope=global',{},access=token)
    response=JSONResponse({'status':'signed_out'})
    response.delete_cookie(ACCESS_COOKIE,path='/')
    response.delete_cookie(REFRESH_COOKIE,path='/auth')
    response.headers['Cache-Control']='no-store'
    return response
