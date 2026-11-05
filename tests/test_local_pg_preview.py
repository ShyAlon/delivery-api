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


def test_owned_controller_initializes_actual_schema_and_checksum_ledger(tmp_path,monkeypatch):
    """Fresh owned real PG, not only mocks of migration/role initialization."""
    import pgserver,psycopg
    service=service_module();monkeypatch.setenv('REQUI_RESOURCE_NONCE','b'*32)
    server=pgserver.get_server(str(tmp_path/'pg'),cleanup_mode='delete')
    try:
        root=tmp_path/'contract';root.mkdir()
        import shutil
        shutil.copyfile(ROOT/'tests/pg_harness.py',root/'pg_contract.py')
        shutil.copyfile(ROOT/'src/delivery_api/runtime_roles.py',root/'runtime_roles.py')
        shutil.copytree(ROOT/'supabase/migrations',root/'migrations')
        # Only connection identity check is replaced: pgserver uses its own
        # private socket/name. Actual migration SQL and checksums run unchanged.
        def ownership(conn):
            assert conn.execute('SELECT nonce FROM local_controller.identity WHERE singleton').fetchone()==('b'*32,)
        monkeypatch.setattr(service,'verify',ownership)
        with psycopg.connect(server.get_uri()) as conn:
            service.migrate(conn,root)
            count=conn.execute('SELECT count(*) FROM local_controller.migrations').fetchone()[0]
            assert count>50
            assert conn.execute('SELECT count(*) FROM auth.users').fetchone()[0]==0
            service.migrate(conn,root)
            assert conn.execute('SELECT count(*) FROM local_controller.migrations').fetchone()[0]==count
            first=sorted((root/'migrations').glob('*.sql'))[0]
            first.write_text(first.read_text()+'\n-- changed applied source\n')
            with pytest.raises(ValueError,match='migration changed'):service.migrate(conn,root)
    finally:server.cleanup()


def test_controller_refuses_networked_or_replaced_mounts(monkeypatch):
    import local_pg_preview as module
    n='c'*32;r=names(n)
    base={'State':{'Running':True},'Mounts':[{'Type':'volume','Destination':'/var/lib/postgresql/data','Name':r['data']},{'Type':'volume','Destination':'/owned','Name':r['socket']}], 'HostConfig':{'PortBindings':{},'NetworkMode':'bridge'}}
    monkeypatch.setattr(module,'inspect_owned',lambda *args:base)
    with pytest.raises(WorkflowError,match='no network'):module.controller(r,n,{'action':'check'})
    base['HostConfig']['NetworkMode']='none';base['Mounts'][0]['Name']='supabase-production'
    with pytest.raises(WorkflowError,match='mount identity'):module.controller(r,n,{'action':'check'})


def test_app_start_refuses_external_dsn_before_any_docker_access(tmp_path,monkeypatch):
    import local_pg_preview as module
    n='d'*32;r=names(n)
    monkeypatch.setattr(module,'controller',lambda *args:pytest.fail('must refuse before DB access'))
    with pytest.raises(WorkflowError,match='owned local socket'):
        module.start_app(r,n,'sha256:'+'e'*64,source(tmp_path),8002)


def test_first_approved_pin_can_install_previously_unready_runtime(monkeypatch):
    from delivery_api import app as module,cloud_runtime
    calls=[]
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:False)
    monkeypatch.setattr(cloud_runtime,'install',lambda *args,**kwargs:calls.append((args,kwargs)))
    monkeypatch.setattr(cloud_runtime,'_failure','previous missing pins')
    module._activate_reviewed_pins()
    assert len(calls)==1 and calls[0][1]['watch_vendor'] is True
    assert cloud_runtime._failure is None


def test_debug_zip_is_bounded_owned_and_excludes_settings(tmp_path,monkeypatch):
    import local_pg_preview as module
    from types import SimpleNamespace
    import zipfile
    n='e'*32;r=names(n)
    monkeypatch.setattr(module,'inspect_owned',lambda *args:{'Id':'id','Image':'sha256:'+'f'*64,'State':{'Running':True},'Config':{'Env':['secret=do-not-copy']}})
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(stdout='postgresql://user:private@host/db sk-abcdefghijklmno',stderr=''))
    path=module.diagnostics(r,n,tmp_path)
    assert not path.stat().st_mode&0o077
    with zipfile.ZipFile(path) as archive:
        text='\n'.join(archive.read(name).decode() for name in archive.namelist())
        assert 'do-not-copy' not in text and 'user:private' not in text and 'sk-abcdefgh' not in text


def test_browser_enrolls_before_first_app_read_and_revokes_on_close(monkeypatch):
    """Actual browser/cookie/response interception. External Auth is fictional."""
    import threading
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    import local_pg_signin as module
    owner=str(uuid.uuid4());sid=str(uuid.uuid4());events=[];reads=[]
    identity={'sub':owner,'session_id':sid,'email':'fictional@example.test','exp':time.time()+60,'iat':time.time()}
    monkeypatch.setattr(module,'verified_identity',lambda *args,**kwargs:identity)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/api/auth':
                reads.append('enroll' in events)
                body=json.dumps({'authenticated':'enroll' in events}).encode()
            else:body=b'<html><body>Fictional local sign-in fixture</body></html>'
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.send_response(200);self.send_header('Set-Cookie','requi_access=fictional-token; HttpOnly; Path=/; SameSite=Lax')
            body=b'{"status":"signed_in"}';self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def controller(payload):events.append(payload['action']);return {'enrolled':True}
    def interact(page):
        result=page.evaluate("async()=>{await fetch('/auth/signin',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'fictional@example.test',password:'test-only'})});return (await fetch('/api/auth')).json()}")
        assert result['authenticated'] is True
        page.close()
    try:
        module.browse('http://127.0.0.1:'+str(server.server_port),{'DELIVERY_SUPABASE_URL':'https://fictional.supabase.co','DELIVERY_SUPABASE_ANON_KEY':'public','REQUI_OPERATOR_SUBJECT':owner,'REQUI_OPERATOR_REGISTRY_VERSION':'owned-local-v1','REQUI_OPERATOR_CAPABILITIES':'[]'},controller,headless=True,on_open=interact)
        assert reads==[True] and events==['enroll','revoke_all']
    finally:server.shutdown();server.server_close();thread.join(5)


def test_mac_gate_is_one_repo_owned_entry_and_has_no_private_settings():
    text=(ROOT/'scripts/ops/test-local-pg.sh').read_text()
    assert 'set -euo pipefail' in text
    assert 'bash scripts/ops/image.sh --accept-local' in text
    assert 'bash scripts/ops/image.sh --local-pg-check' in text
    assert '--publish' not in text and '--deploy' not in text and 'preview.env' not in text


def test_source_auth_settings_need_no_database_or_engine_token(tmp_path):
    path=tmp_path/'auth-only.env'
    path.write_text('DELIVERY_SUPABASE_URL=https://test.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=public\nREQUI_OPERATOR_SUBJECT=00000000-0000-0000-0000-000000000001\n')
    path.chmod(0o600)
    result=settings(path,'a'*32,names('a'*32),8002)
    assert result['DELIVERY_DATABASE_URL']==DSN
    assert len(result['INTERVIEW_ENGINE_TOKEN'])>=24
    assert result['DELIVERY_SUPABASE_URL']=='https://test.supabase.co'


def test_source_settings_error_is_typed_and_has_no_private_value(tmp_path):
    path=tmp_path/'bad.env';path.write_text('DELIVERY_SUPABASE_URL=http://private.invalid\n');path.chmod(0o600)
    with pytest.raises(WorkflowError,match='Local source settings invalid') as error:
        settings(path,'a'*32,names('a'*32),8002)
    assert 'private.invalid' not in str(error.value)
