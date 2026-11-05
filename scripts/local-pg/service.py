"""Owned persistent PostgreSQL dependency. Unix socket only, no caller DSN."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

DATA=Path('/var/lib/postgresql/data')
SOCKET=Path('/owned/socket')
DATABASE='requi_local'
NONCE=re.compile(r'[a-f0-9]{32}\Z')


def nonce():
    value=os.environ.get('REQUI_RESOURCE_NONCE','')
    if not NONCE.fullmatch(value):raise ValueError('owned resource identity required')
    return value


def connect():
    import psycopg
    # Never parameterized from user input, environment or settings.
    return psycopg.connect(dbname=DATABASE,user='postgres',host=str(SOCKET),connect_timeout=5)


def pg(command,*args):
    out=subprocess.run([command,*args],capture_output=True,text=True,timeout=60)
    if out.returncode:raise RuntimeError('owned PostgreSQL operation failed: '+command)


def verify(conn):
    row=conn.execute('SELECT nonce FROM local_controller.identity WHERE singleton').fetchone()
    if row!=(nonce(),):raise ValueError('local database ownership differs')
    if conn.info.host!=str(SOCKET) or conn.info.dbname!=DATABASE or conn.info.user!='postgres':
        raise ValueError('owned Unix socket connection required')


def migrate(conn,root=Path('/controller')):
    import importlib.util
    spec=importlib.util.spec_from_file_location('pg_contract',root/'pg_contract.py')
    contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
    exists=conn.execute("SELECT to_regclass('local_controller.identity')").fetchone()[0]
    if not exists:
        # No test-owner seeds, default test sessions or application grant shortcuts.
        conn.execute('CREATE ROLE sandbox LOGIN SUPERUSER; CREATE ROLE delivery_app LOGIN NOSUPERUSER NOBYPASSRLS; CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS')
        conn.execute('SET ROLE sandbox')
        conn.execute(contract.SUPABASE_STAND_IN)
        conn.execute(contract.SUPABASE_DEFAULT_ACL)
        conn.execute('CREATE SCHEMA local_controller; REVOKE ALL ON SCHEMA local_controller FROM PUBLIC; CREATE TABLE local_controller.identity(singleton boolean PRIMARY KEY CHECK(singleton),nonce text NOT NULL); CREATE TABLE local_controller.migrations(name text PRIMARY KEY,sha256 text NOT NULL); CREATE TABLE local_controller.sessions(session_id uuid PRIMARY KEY,owner_id uuid NOT NULL,expires_at double precision NOT NULL)')
        conn.execute('INSERT INTO local_controller.identity VALUES(true,%s)',(nonce(),))
        conn.execute('RESET ROLE');conn.commit()
    verify(conn)
    for path in sorted((root/'migrations').glob('*.sql')):
        if not re.match(r'^\d{14}_',path.name):continue
        checksum=hashlib.sha256(path.read_bytes()).hexdigest()
        old=conn.execute('SELECT sha256 FROM local_controller.migrations WHERE name=%s',(path.name,)).fetchone()
        if old:
            if old[0]!=checksum:raise ValueError('applied local migration changed: '+path.name)
            continue
        conn.execute('SET ROLE sandbox')
        conn.execute(path.read_text())
        conn.execute('INSERT INTO local_controller.migrations VALUES(%s,%s)',(path.name,checksum))
        conn.execute('RESET ROLE');conn.commit()
    spec=importlib.util.spec_from_file_location('runtime_roles',root/'runtime_roles.py')
    roles=importlib.util.module_from_spec(spec);spec.loader.exec_module(roles)
    for role in roles.RUNTIME_ROLES:
        conn.execute('GRANT "'+role+'" TO delivery_app WITH INHERIT FALSE, SET TRUE')
    conn.commit()


def revoke(conn,session=None):
    verify(conn)
    if session:
        conn.execute('DELETE FROM auth.sessions WHERE id IN (SELECT session_id FROM local_controller.sessions WHERE session_id=%s)',(session,))
        conn.execute('DELETE FROM local_controller.sessions WHERE session_id=%s',(session,))
    else:
        conn.execute('DELETE FROM auth.sessions WHERE id IN (SELECT session_id FROM local_controller.sessions WHERE expires_at<=extract(epoch FROM clock_timestamp()))')
        conn.execute('DELETE FROM local_controller.sessions WHERE expires_at<=extract(epoch FROM clock_timestamp())')
    conn.commit()


def enroll(conn,identity,config):
    """Input has been verified by the owner-side strict hosted JWT + /user check.

    This private controller is the only writer. The app never gets its admin
    peer identity or a TCP/admin DSN. Nothing is fetched from a hosted database.
    """
    import uuid
    if set(identity)!={'sub','session_id','email','exp','iat'}:raise ValueError('bounded verified identity required')
    owner=str(uuid.UUID(identity['sub']));session=str(uuid.UUID(identity['session_id']))
    if identity['sub']!=config.get('subject'):raise ValueError('reviewed local operator required')
    if not isinstance(identity['email'],str) or not 3<=len(identity['email'])<=254:raise ValueError('signed email required')
    exp=identity['exp']
    if type(exp) not in (int,float) or not time.time()<exp<=time.time()+86400:raise ValueError('live bounded access credential required')
    verify(conn)
    conn.execute('INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(%s,%s,now()) ON CONFLICT(id) DO UPDATE SET email=excluded.email',(owner,identity['email']))
    conn.execute('INSERT INTO auth.sessions(id,user_id,not_after) VALUES(%s,%s,to_timestamp(%s)) ON CONFLICT(id) DO UPDATE SET not_after=excluded.not_after WHERE auth.sessions.user_id=excluded.user_id',(session,owner,exp))
    conn.execute('INSERT INTO local_controller.sessions VALUES(%s,%s,%s) ON CONFLICT(session_id) DO UPDATE SET expires_at=excluded.expires_at WHERE local_controller.sessions.owner_id=excluded.owner_id',(session,owner,exp))
    if set(config)!={'subject','version','capabilities'} or config['subject']!=owner:raise ValueError('reviewed local operator binding required')
    caps=config['capabilities']
    allowed={'analytics.read','sessions.read','debug.read','logs.read','usage.read','payments.read','services.operate','sessions.operate'}
    if not isinstance(caps,list) or set(caps)!=allowed:raise ValueError('exact local review capability inventory required')
    conn.execute('INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,%s,%s,true) ON CONFLICT(subject) DO NOTHING',(owner,config['version'],caps))
    # Local test credits, never a purchase or copied hosted balance. Existing
    # balances/limits are retained on every subsequent sign-in, never topped up.
    conn.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,100000000000,1000000000,extract(epoch FROM clock_timestamp())) ON CONFLICT(owner_id) DO NOTHING',(owner,))
    conn.execute("INSERT INTO admission_limits VALUES(%s,'owned-local-v1',86400,NULL,NULL,NULL,NULL,60,NULL,3) ON CONFLICT(owner_id) DO NOTHING",(owner,))
    conn.execute("INSERT INTO own_test_expense_profiles VALUES(%s,'own-test-2usd-rolling24h-v1',2000000000000,1000000000000) ON CONFLICT(owner_id) DO NOTHING",(owner,))
    conn.execute("INSERT INTO expense_guard_policy VALUES(true,'confirmed-3usd-session-platform-v1',3000000000000,3000000000000,1000000000000,3) ON CONFLICT(singleton) DO NOTHING")
    if not conn.execute('SELECT 1 FROM operator_expense_policy_head WHERE singleton').fetchone():
        conn.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES('owned-local-initial',4000000000000,1000000000000,%s,'Reviewed local setup; does not start paid work')",(owner,))
        conn.execute("INSERT INTO operator_expense_policy_head VALUES(true,'owned-local-initial')")
    conn.commit()
    return {'enrolled':True,'owner':owner,'session_id':session,'expires_at':exp}


def serve():
    if any(os.environ.get(k) for k in ('DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DEEPSEEK_API_KEY')):
        raise ValueError('dependency refuses inherited database/provider settings')
    nonce();DATA.mkdir(parents=True,exist_ok=True);SOCKET.mkdir(parents=True,exist_ok=True)
    marker=DATA/'requi-owner'
    if (DATA/'PG_VERSION').exists():
        if not marker.is_file() or marker.read_text()!=nonce():raise ValueError('persistent volume ownership differs')
    else:
        if any(DATA.iterdir()):raise ValueError('nonempty unowned database volume')
        pg('initdb','-D',str(DATA),'--auth-local=peer','--auth-host=reject','--encoding=UTF8','--no-locale')
        marker.write_text(nonce());marker.chmod(0o600)
    (DATA/'pg_hba.conf').write_text('local all all peer map=requi_owned\nhost all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n')
    (DATA/'pg_ident.conf').write_text('requi_owned postgres postgres\nrequi_owned requi_app delivery_app\n')
    pg('pg_ctl','-D',str(DATA),'-l',str(DATA/'server.log'),'-o','-h "" -k '+str(SOCKET)+' -c unix_socket_permissions=0777','-w','start')
    try:
        import psycopg
        with psycopg.connect(dbname='postgres',user='postgres',host=str(SOCKET),autocommit=True) as conn:
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(DATABASE,)).fetchone():conn.execute('CREATE DATABASE '+DATABASE)
        with connect() as conn:migrate(conn)
        stop=[False]
        def finish(*_):stop[0]=True
        signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
        print('owned local PostgreSQL ready; TCP disabled',flush=True)
        while not stop[0]:
            with connect() as conn:revoke(conn)
            time.sleep(1)
    finally:pg('pg_ctl','-D',str(DATA),'-m','fast','-w','stop')


def main():
    if sys.argv[1:] == ['serve']:return serve()
    raw=sys.stdin.buffer.read(32769)
    if len(raw)>32768:raise ValueError('bounded controller input required')
    request=json.loads(raw)
    with connect() as conn:
        verify(conn)
        if request['action']=='check':result={'ready':True,'nonce':nonce(),'tcp':False}
        elif request['action']=='reset_check':
            paid=conn.execute("SELECT count(*) FROM provider_attempts WHERE quote->>'provider'='deepseek' AND state NOT IN ('cancelled','released')").fetchone()[0]
            live=conn.execute("SELECT count(*) FROM worker_jobs WHERE state IN ('pending','running')").fetchone()[0]
            result={'reset_allowed':paid==0 and live==0,'paid_evidence_rows':paid,'active_job_rows':live}
        elif request['action']=='enroll':result=enroll(conn,request['identity'],request['config'])
        elif request['action']=='revoke_all':
            conn.execute('DELETE FROM auth.sessions WHERE id IN (SELECT session_id FROM local_controller.sessions)');conn.execute('DELETE FROM local_controller.sessions');conn.commit();result={'revoked_all':True}
        elif request['action']=='revoke':revoke(conn,request['session_id']);result={'revoked':True}
        else:raise ValueError('unknown controller action')
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Local PostgreSQL controller refused: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(2)
