import json,logging
from types import SimpleNamespace
import httpx
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


def refuse(monkeypatch,status,body):
    monkeypatch.setattr(config,'SUPABASE_URL','https://x.example')
    monkeypatch.setattr(config,'SUPABASE_ANON_KEY','k')
    content=body if isinstance(body,bytes) else json.dumps(body).encode()
    monkeypatch.setattr(sessions.httpx,'post',lambda *a,**k:httpx.Response(status,content=content))


@pytest.mark.parametrize('status,body,expect_status,expect_text',[
    (400,{'error_code':'invalid_credentials','msg':'Invalid login credentials'},401,'Email or password is not right, or no account exists yet'),
    (400,{'error_code':'email_not_confirmed'},401,'Confirm your email first, then sign in'),
    (429,{'error_code':'over_request_rate_limit'},429,'Too many attempts, wait a minute and try again'),
    (401,{'message':'Invalid API key'},503,'Sign-in service is not set up correctly'),
    (500,b'<html>',503,'Sign-in service is not set up correctly'),
    (400,{'error_code':'refresh_token_not_found'},401,'Sign-in failed or session expired'),
])
def test_signin_refusals_say_what_to_do(monkeypatch,status,body,expect_status,expect_text):
    refuse(monkeypatch,status,body)
    with pytest.raises(HTTPException) as e:sessions.signin('a@b.example','pw-secret')
    assert (e.value.status_code,e.value.detail)==(expect_status,expect_text)


def test_weak_password_on_signup(monkeypatch):
    refuse(monkeypatch,422,{'error_code':'weak_password'})
    with pytest.raises(HTTPException) as e:sessions.signup('a@b.example','pw')
    assert e.value.status_code==422


def test_log_has_status_and_code_only(monkeypatch,caplog):
    refuse(monkeypatch,400,{'error_code':'invalid_credentials','msg':'Invalid login credentials for a@b.example','access_token':'tok-secret'})
    with caplog.at_level(logging.WARNING),pytest.raises(HTTPException):sessions.signin('a@b.example','pw-secret')
    text=caplog.text
    assert 'status=400' in text and 'code=invalid_credentials' in text
    assert 'a@b.example' not in text and 'pw-secret' not in text and 'tok-secret' not in text
