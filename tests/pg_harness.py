"""A throwaway real Postgres for the database tests, so they run instead of skipping.

Starts a private Postgres 16 (the `pgserver` package, in a temp directory, deleted at exit), stands in for the
parts of Supabase that migrations rely on (the auth schema, auth.uid(), the usual roles), applies every file in
supabase/migrations in order, and publishes its address as DELIVERY_TEST_POSTGRES_DSN.

Rules:
- An address already in DELIVERY_TEST_POSTGRES_DSN is used as is; nothing is started.
- DELIVERY_TEST_NO_POSTGRES=1 turns the harness off (the database tests skip, as before).
- If `pgserver` is not installed the database tests skip, as before.
- If migrations do not apply, the database tests skip and test_pg_harness fails loudly with the reason.
It never touches a real database: it only ever connects to its own temporary server."""
import atexit
import os
import re
import shutil
import tempfile
from pathlib import Path

MIGRATIONS=Path(__file__).resolve().parents[1]/'supabase'/'migrations'
STATE={'status':'off','reason':'','applied':[],'dsn':None}

ROLES="""
CREATE ROLE sandbox LOGIN SUPERUSER; CREATE ROLE delivery_app LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS;
"""
SUPABASE_STAND_IN="""
CREATE SCHEMA auth;
CREATE TABLE auth.users(id UUID PRIMARY KEY, email TEXT, email_confirmed_at TIMESTAMPTZ, raw_user_meta_data JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE auth.sessions(id UUID PRIMARY KEY, user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ DEFAULT now(), not_after TIMESTAMPTZ);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$ SELECT COALESCE(NULLIF(current_setting('request.jwt.claims',true),''),'{}')::jsonb $$;
GRANT USAGE ON SCHEMA auth TO anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION auth.uid(),auth.jwt() TO anon,authenticated,service_role;
"""


SUPABASE_DEFAULT_ACL="""
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT ALL ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT ALL ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE sandbox IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO service_role;
"""   # Supabase grants ALL on new public tables / EXECUTE on new functions to these roles via pg_default_acl; model it so least-privilege regressions fail here the way the live project behaves (live abort 2026-10-24: anon held INSERT on artifact_bytes).


SEED="""
INSERT INTO auth.users(id,email,email_confirmed_at) VALUES
 ('00000000-0000-0000-0000-000000000001','owner-a@example.test',now()),
 ('00000000-0000-0000-0000-000000000002','owner-b@example.test',now());
INSERT INTO auth.sessions(id,user_id) VALUES
 ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-000000000001'),
 ('00000000-0000-0000-0000-0000000000a2','00000000-0000-0000-0000-000000000002');
"""   # the two owners and their live sessions the database tests are written against (tests A and B).
# Session ids are deliberately DISTINCT from user ids: session authority must never
# pass because a synthetic claim reused the owner UUID (vacuous policy coverage).


def _numbered(path):
    return re.match(r'^\d{14}_',path.name) is not None


def start(*,database_prefix='test_tenant_',socket_directory=None,server_factory=None):
    # The dedicated item6 runner needs its existing stricter isolation contract.
    # These are fixture-only choices, never credentials or a caller-supplied DSN.
    if database_prefix not in ('test_tenant_','requi_item6_'):
        raise ValueError('known disposable database prefix required')
    if socket_directory is not None and (database_prefix!='requi_item6_' or socket_directory!=Path('/tmp')):
        raise ValueError('item6 disposable socket directory required')
    # Repeated in-process pytest runs reuse our own migrated server. Do not
    # relabel it external merely because start() published its DSN last time.
    if STATE['status']=='running' and os.environ.get('DELIVERY_TEST_POSTGRES_DSN')==STATE['dsn']:
        return
    if os.environ.get('DELIVERY_TEST_NO_POSTGRES')=='1':
        STATE.update(status='off',reason='DELIVERY_TEST_NO_POSTGRES=1');return
    if os.environ.get('DELIVERY_TEST_POSTGRES_DSN'):
        STATE.update(status='external',reason='DELIVERY_TEST_POSTGRES_DSN was already set',dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN']);return
    try:
        import psycopg
        if server_factory is None:import pgserver
    except ImportError as exc:
        STATE.update(status='missing',reason='pgserver or psycopg is not installed (%s); database tests will skip'%exc.name);return
    scratch=tempfile.mkdtemp(prefix='delivery-api-pg-')
    try:
        if server_factory is not None:
            server=server_factory(scratch,cleanup_mode='delete')
        elif socket_directory is None:
            server=pgserver.get_server(scratch,cleanup_mode='delete')
        else:
            # pgserver chooses a free port; only the socket directory is fixed.
            from unittest.mock import patch
            with patch('pgserver.postgres_server.find_suitable_socket_dir',return_value=socket_directory):
                server=pgserver.get_server(scratch,cleanup_mode='delete')
    except Exception as exc:
        shutil.rmtree(scratch,ignore_errors=True)
        STATE.update(status='failed',reason='the temporary Postgres did not start: %s: %s'%(type(exc).__name__,str(exc)[:300]));return
    from pg_guardian import protect
    try:close_guardian = protect(scratch,ctl=getattr(server,'ctl',None))
    except BaseException:
        server.cleanup()
        shutil.rmtree(scratch,ignore_errors=True)
        raise
    def cleanup():
        try:close_guardian()
        finally:
            server.cleanup()
            shutil.rmtree(scratch,ignore_errors=True)
    atexit.register(cleanup)
    uri=server.get_uri()
    socket_dir=re.search(r'host=([^&]+)',uri)
    if not socket_dir:
        STATE.update(status='failed',reason='the temporary Postgres has no socket directory in %s'%uri);return
    import uuid
    dbname=database_prefix+uuid.uuid4().hex[:12]   # several tests refuse to run unless the database name starts with test_tenant_
    dsn='host=%s dbname=%s user=sandbox'%(socket_dir.group(1),dbname)   # the tests swap user=sandbox for user=delivery_app
    try:
        with psycopg.connect(uri,autocommit=True) as admin:
            admin.execute('CREATE DATABASE "%s"'%dbname)
            admin.execute(ROLES)   # roles belong to the whole server, not one database
        with psycopg.connect(dsn,autocommit=True) as conn:
            conn.execute(SUPABASE_STAND_IN)
            conn.execute(SUPABASE_DEFAULT_ACL)
            files=sorted(p for p in MIGRATIONS.glob('*.sql') if _numbered(p))
            if not files:
                STATE.update(status='failed',reason='no migration files found in %s'%MIGRATIONS);return
            for path in files:
                try:conn.execute(path.read_text())
                except Exception as exc:
                    STATE.update(status='failed',reason='%s did not apply: %s: %s'%(path.name,type(exc).__name__,str(exc).strip()[:400]),applied=list(STATE['applied']));return
                STATE['applied'].append(path.name)
            # Mirror the real owner installer exactly; wildcard grants masked
            # missing production memberships such as payment-read and log-write.
            from delivery_api.runtime_roles import RUNTIME_ROLES
            for role in RUNTIME_ROLES:
                conn.execute('GRANT "%s" TO delivery_app WITH INHERIT FALSE, SET TRUE'%role)
            try:conn.execute(SEED)
            except Exception as exc:
                STATE.update(status='failed',reason='seeding the test owners failed: %s: %s'%(type(exc).__name__,str(exc).strip()[:300]));return
    except Exception as exc:
        STATE.update(status='failed',reason='%s: %s'%(type(exc).__name__,str(exc)[:300]));return
    # Some tests shell out to psql. Use the copy that ships with pgserver, so nothing has to be installed on the developer's machine.
    try:
        import pgserver
        bindir=Path(pgserver.__file__).parent/'pginstall'/'bin'
        if (bindir/'psql').exists():os.environ['PATH']=str(bindir)+os.pathsep+os.environ.get('PATH','')
    except Exception:pass
    os.environ['DELIVERY_TEST_POSTGRES_DSN']=dsn
    STATE.update(status='running',dsn=dsn)
