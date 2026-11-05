"""Owner Docker-host gate. Fictional owner, isolated PG; no hosted auth or model.

Exercises fresh/restarted persistent volume, app UID peer login, actual tenant
RLS, local enrollment/revocation and remote/admin peer refusal. Never uses the
owner's local-preview identity/data volumes or reads source settings.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from image_workflow import WorkflowError,command,confirm,log
from local_pg_preview import accepted_image,names,ensure_dependency,controller,inspect_owned,LABEL,settings,DSN


def gate(root,state):
    image=accepted_image(state)
    if not confirm('run isolated local PostgreSQL Docker gate with fictional owner; no hosted account, model or existing data'):raise WorkflowError('declined')
    nonce=uuid.uuid4().hex;resource=names(nonce)
    owner='00000000-0000-0000-0000-000000000001';session='00000000-0000-0000-0000-0000000000a1'
    from delivery_api.backoffice_access import DEFAULT_CAPABILITIES
    identity={'sub':owner,'session_id':session,'email':'fictional@example.test','iat':int(time.time()),'exp':int(time.time())+120}
    config={'subject':owner,'version':'owned-local-v1','capabilities':sorted(DEFAULT_CAPABILITIES|{'sessions.operate'})}
    diagnostic=Path.home()/'.local/share/requi/local-pg-builds'/('gate-'+nonce+'.log')
    # Real owner source-file shape: hosted Auth/operator values only, no DB,
    # admin URL or engine token. Derived local runtime must validate separately.
    with tempfile.TemporaryDirectory(prefix='requi-local-auth-source-') as temp:
        authfile=Path(temp)/'auth.env'
        authfile.write_text('DELIVERY_SUPABASE_URL=https://fictional.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=fictional-public\nREQUI_OPERATOR_SUBJECT='+owner+'\n')
        authfile.chmod(0o600)
        generated=settings(authfile,nonce,resource,8002)
        if generated['DELIVERY_DATABASE_URL']!=DSN:raise WorkflowError('auth-only source derived wrong database')
        runtime=Path(temp)/'runtime.env';runtime.write_text(''.join(k+'='+v+'\n' for k,v in generated.items()));runtime.chmod(0o600)
        from image_settings import validate
        validate(runtime)
    log('PASS','Auth-only owner source settings accepted; owned runtime database/token derived and independently validated')
    def run(code):
        # Same exact accepted image, UID10001 and RO socket. No key, real env,
        # network, host data, production container or database URL is accepted.
        result=subprocess.run(['docker','run','--rm','--network','none','--user','10001:10001','--read-only',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--mount','type=volume,source='+resource['socket']+',target=/owned,readonly',
            '--entrypoint','python',image,'-c',code],capture_output=True,text=True,timeout=60)
        if result.returncode:
            from image_receipt import atomic_write
            atomic_write(diagnostic,(result.stdout+result.stderr).encode())
            raise WorkflowError('Local PG exact-image gate failed; private fictional-fixture log: '+str(diagnostic))
        return result.stdout.strip()
    try:
        ensure_dependency(root,resource,nonce)
        controller(resource,nonce,{'action':'enroll','identity':identity,'config':config})
        prefix="import psycopg; c=psycopg.connect('postgresql:///requi_local?host=/owned/socket&user=delivery_app'); c.execute('SET LOCAL ROLE requi_backend'); "
        claims="c.execute(\"SELECT set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)\",('"+owner+"','"+session+"')); "
        assert run(prefix+claims+"assert c.execute('SELECT public.requi_session_active()').fetchone()[0] is True; print('active')")=='active'
        command(['docker','stop','--time','30',resource['db']])
        command(['docker','start',resource['db']])
        for _ in range(120):
            try:
                controller(resource,nonce,{'action':'check'});break
            except WorkflowError:time.sleep(1)
        else:raise WorkflowError('persistent local PG restart failed')
        assert run(prefix+claims+"assert c.execute('SELECT public.requi_session_active()').fetchone()[0] is True; print('persisted')")=='persisted'
        # Neither the app UID nor socket mount gives a superuser login.
        denied=run("import psycopg\ntry:\n psycopg.connect('postgresql:///requi_local?host=/owned/socket&user=postgres',connect_timeout=3)\nexcept psycopg.OperationalError:\n print('denied')\nelse:\n raise AssertionError('admin peer escaped')")
        if denied!='denied':raise WorkflowError('admin peer refusal not proved')
        controller(resource,nonce,{'action':'revoke','session_id':session})
        assert run(prefix+claims+"assert c.execute('SELECT public.requi_session_active()').fetchone()[0] is False; print('revoked')")=='revoked'
        log('PASS','Local PG Docker gate: exact accepted app UID peer/RLS, persistence restart, admin refusal and revoked-session denial. Hosted Auth and full provider flow remain separate proofs.')
    except BaseException:
        try:
            from local_pg_preview import diagnostics
            diagnostics(resource,nonce,diagnostic.parent)
        except Exception:log('WARN','Owned gate diagnostic capture unavailable')
        raise
    finally:
        # Only nonce-bound gate resources, never real local-preview data.
        for kind,key in [('container','db'),('volume','socket'),('volume','data')]:
            try:
                if inspect_owned(kind,resource[key],nonce):
                    command(['docker','rm','--force',resource[key]] if kind=='container' else ['docker','volume','rm',resource[key]])
            except WorkflowError:log('WARN','Isolated gate cleanup unconfirmed; retain owned resource evidence')
