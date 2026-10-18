"""Same-origin browser session bridge to Supabase Auth.

Access/refresh tokens stay in HttpOnly cookies, never browser storage or URLs.
Passwords pass directly to Supabase and are never persisted or logged here.
"""
import logging
import re

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from . import config

ACCESS_COOKIE='requi_access'
REFRESH_COOKIE='requi_refresh'


_CODE=re.compile(r'[a-z][a-z0-9_]{0,63}')


def _error_code(response):
    try:body=response.json()
    except Exception:return 'unreadable'
    if not isinstance(body,dict):return 'unreadable'
    for key in ('error_code','code','error'):
        value=body.get(key)
        if isinstance(value,str) and _CODE.fullmatch(value):return value
    return 'unknown'


def _refusal(status,code):
    """The message a person can act on, without confirming whether an email has an account."""
    if code=='email_not_confirmed':return HTTPException(401,'Confirm your email first, then sign in')
    if code=='invalid_credentials':return HTTPException(401,'Email or password is not right, or no account exists yet')
    if code=='weak_password':return HTTPException(422,'That password is too weak, choose a longer one')
    if status==429 or code=='over_request_rate_limit':return HTTPException(429,'Too many attempts, wait a minute and try again')
    if status in (401,403) and code in ('invalid_api_key','unknown','unreadable') or status>=500:
        return HTTPException(503,'Sign-in service is not set up correctly')
    return HTTPException(401,'Sign-in failed or session expired')


def _call(path,payload,*,access=None):
    headers={'apikey':config.SUPABASE_ANON_KEY,'Content-Type':'application/json'}
    if access:headers['Authorization']='Bearer '+access
    try:
        response=httpx.post(config.SUPABASE_URL+'/auth/v1/'+path,
                            headers=headers,json=payload,timeout=10)
        if response.status_code>=400:
            # Log only the status and Supabase's short error code. Never the response body, tokens or the email.
            code=_error_code(response)
            logging.getLogger(__name__).warning('Supabase auth %s refused: status=%s code=%s',path.split('?')[0],response.status_code,code)
            raise _refusal(response.status_code,code)
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
