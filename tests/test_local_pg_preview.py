"""Local dependency safety, no Docker/network/hosted account or paid work."""
import importlib.util
import json
from pathlib import Path
import sys
import time
import uuid
import httpx
import pytest

from policy_database import isolated_policy_database

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/docker'))
from local_pg_preview import DSN,DB_KEYS,settings,require_owned,names,identity,WorkflowError
from local_pg_signin import verified_identity


def source(tmp_path,**extra):
    values={'DELIVERY_DATABASE_URL':'postgresql://production.invalid/never-connect',
      'REQUI_ADMIN_DATABASE_URL':'postgresql://production.invalid/never-connect',
      'DELIVERY_SUPABASE_URL':'https://test.supabase.co','DELIVERY_SUPABASE_ANON_KEY':'public',
      'INTERVIEW_ENGINE_TOKEN':'a'*32,'REQUI_OPERATOR_SUBJECT':'00000000-0000-0000-0000-000000000001',
      'PAYPAL_SECRET':'must-not-cross','DELIVERY_PAYMENTS_ENABLED':'1',
      'INTERVIEW_ENGINE_SESSIONS_DSN':'postgresql://production.invalid/never-connect'}
    values.update(extra)
    path=tmp_path/'source.env';path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));path.chmod(0o600)
    return path


def test_only_owned_local_dsn_crosses_and_no_payment_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('DELIVERY_DATABASE_URL','postgresql://evil.invalid/never')
    nonce='a'*32
    result=settings(source(tmp_path),nonce,names(nonce),8002)
    assert result['DELIVERY_DATABASE_URL']==DSN
    assert not any(k in result for k in DB_KEYS-{'DELIVERY_DATABASE_URL'})
    assert 'PAYPAL_SECRET' not in result and 'DELIVERY_PAYMENTS_ENABLED' not in result
    assert 'production.invalid' not in str(result) and 'evil.invalid' not in str(result)
    assert result['DELIVERY_PUBLIC_ORIGIN']=='http://127.0.0.1:8002'


def test_no_implicit_operator(tmp_path):
    with pytest.raises(WorkflowError,match='reviewed REQUI_OPERATOR_SUBJECT'):
        settings(source(tmp_path,REQUI_OPERATOR_SUBJECT=''),'a'*32,names('a'*32),8002)


def test_ownership_replacement_never_passes():
    value={'Name':'/requi-db','Config':{'Labels':{'requi.local-pg-owner':'a'*32}}}
    require_owned(value,'a'*32,'container','requi-db')
    with pytest.raises(WorkflowError):require_owned(value,'b'*32,'container','requi-db')
    with pytest.raises(WorkflowError):require_owned(value,'a'*32,'container','supabase')


def test_identity_is_persistent_and_private(tmp_path):
    path=tmp_path/'identity.json';first=identity(path)
    assert first==identity(path) and not path.stat().st_mode&0o077
    path.chmod(0o644)
    with pytest.raises(WorkflowError):identity(path)


def test_live_hosted_user_and_strict_verifier_both_required():
    from types import SimpleNamespace
    owner=str(uuid.uuid4());session=str(uuid.uuid4())
    class Verifier:
        def verify_with_email(self,token):return SimpleNamespace(owner_id=owner,session_id=session),False,'owner@example.test'
    import jwt
    token=jwt.encode({'sub':owner,'session_id':session,'exp':time.time()+100,'iat':time.time()},'fictional-only-key-over32-characters',algorithm='HS256')
    requests=[]
    def response(r):
        requests.append(r)
        return httpx.Response(200,json={'id':owner})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        result=verified_identity(token,'https://test.supabase.co',owner,anon_key='public',verifier=Verifier(),client=client)
        assert result['sub']==owner and result['session_id']==session
        assert len(requests)==1 and requests[0].url.path=='/auth/v1/user'
        with pytest.raises(WorkflowError,match='reviewed local operator'):
            verified_identity(token,'https://test.supabase.co',str(uuid.uuid4()),anon_key='public',verifier=Verifier(),client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401))) as client:
        with pytest.raises(WorkflowError,match='Hosted identity check'):
            verified_identity(token,'https://test.supabase.co',owner,anon_key='public',verifier=Verifier(),client=client)


def service_module():
    spec=importlib.util.spec_from_file_location('local_pg_service',ROOT/'scripts/local-pg/service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_enroll_and_expiry_revoke_real_pg_without_credit_topup(isolated_policy_database,monkeypatch):
    import psycopg
    from delivery_api.backoffice_access import DEFAULT_CAPABILITIES
    service=service_module();monkeypatch.setenv('REQUI_RESOURCE_NONCE','a'*32)
    # Owned disposable PG test controls this connection. The production entry
    # requires exact fixed socket/database/user in verify before every mutation.
    monkeypatch.setattr(service,'verify',lambda conn:None)
    owner=str(uuid.uuid4());session=str(uuid.uuid4())
    item={'sub':owner,'session_id':session,'email':'local@example.test','exp':time.time()+60,'iat':time.time()}
    config={'subject':owner,'version':'owned-local-v1','capabilities':sorted(DEFAULT_CAPABILITIES|{'sessions.operate'})}
    with psycopg.connect(isolated_policy_database) as conn:
        conn.execute('CREATE SCHEMA local_controller; CREATE TABLE local_controller.sessions(session_id uuid PRIMARY KEY,owner_id uuid NOT NULL,expires_at double precision NOT NULL)')
        service.enroll(conn,item,config)
        assert conn.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(owner,)).fetchone()[0]==100*10**9
        conn.execute('UPDATE credit_accounts SET balance_units=5000000000 WHERE owner_id=%s',(owner,));conn.commit()
        service.enroll(conn,item,config)
        assert conn.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(owner,)).fetchone()[0]==5*10**9
        conn.execute('UPDATE local_controller.sessions SET expires_at=0');conn.commit()
        service.revoke(conn)
        assert not conn.execute('SELECT 1 FROM auth.sessions WHERE id=%s',(session,)).fetchone()
        assert conn.execute('SELECT 1 FROM auth.users WHERE id=%s',(owner,)).fetchone()
