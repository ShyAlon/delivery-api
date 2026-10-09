from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from delivery_api import supabase_sessions as sessions,config

@pytest.fixture
def calls(monkeypatch):
    seen=[]
    monkeypatch.setattr(config,'SUPABASE_URL','https://test.supabase.co')
    monkeypatch.setattr(config,'SUPABASE_ANON_KEY','public-test-key')
    def post(url,**kwargs):
        seen.append((url,kwargs))
        return SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'access_token':'access','refresh_token':'refresh','expires_in':3600,'expires_at':4000})
    monkeypatch.setattr(sessions.httpx,'post',post)
    return seen


def test_signin_tokens_are_httponly_not_response_body(calls):
    response=sessions.signin('test@invalid','fake-test-password')
    assert b'access_token' not in response.body
    cookies=response.headers.getlist('set-cookie')
    assert len(cookies)==2
    assert all('HttpOnly' in cookie and 'SameSite=lax' in cookie for cookie in cookies)
    assert 'Path=/auth' in cookies[1]
    assert calls[0][0].endswith('/auth/v1/token?grant_type=password')


def test_refresh_rotates_both_without_password(calls):
    sessions.refresh(SimpleNamespace(cookies={sessions.REFRESH_COOKIE:'old-refresh'}))
    assert calls[0][1]['json']=={'refresh_token':'old-refresh'}


def test_missing_refresh_fails_closed(calls):
    with pytest.raises(HTTPException):sessions.refresh(SimpleNamespace(cookies={}))
    assert calls==[]


def test_logout_revokes_global_session_and_clears_cookies(calls):
    response=sessions.logout(SimpleNamespace(cookies={sessions.ACCESS_COOKIE:'access'}))
    assert calls[0][0].endswith('/logout?scope=global')
    assert len(response.headers.getlist('set-cookie'))==2


def test_upstream_failure_never_fallback_or_disclose(monkeypatch,calls):
    monkeypatch.setattr(sessions.httpx,'post',lambda *a,**k:SimpleNamespace(status_code=400,content=b'private detail'))
    with pytest.raises(HTTPException) as err:sessions.signin('test@invalid','fake')
    assert err.value.status_code==401
    assert 'private' not in str(err.value.detail)


def test_signup_confirmation_no_fake_login(monkeypatch,calls):
    monkeypatch.setattr(sessions.httpx,'post',lambda *a,**k:SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'user':{'id':'a'}}))
    response=sessions.signup('test@invalid','fake')
    assert b'confirmation_required' in response.body
    assert not response.headers.getlist('set-cookie')
