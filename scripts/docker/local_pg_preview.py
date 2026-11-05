"""Owner-started persistent local PG, same accepted app image and hosted Auth.

All database operations go through an ownership-checked Docker exec controller.
No caller database URL is accepted or connected to. Source settings contribute
only an explicit allowlist of non-database values.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.parse import urlsplit
from image_receipt import atomic_write,object_json
from image_workflow import WorkflowError,command,confirm,inspect_image,log
from runtime_spec import Target

LABEL='requi.local-pg-owner'
ALLOWED=frozenset({'DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY','OLLAMA_BASE_URL',
    'DELIVERY_OLLAMA_MODEL','DEEPSEEK_API_KEY','REQUI_PIN_PROVIDERS'})
DB_KEYS=frozenset({'DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL','INTERVIEW_ENGINE_SESSIONS_DSN',
    'DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL'})
DSN='postgresql:///requi_local?host=/owned/socket&user=delivery_app'


def identity(path):
    if path.exists():
        if path.is_symlink() or path.stat().st_mode&0o077:raise WorkflowError('private regular local PG identity required')
        value=object_json(path.read_bytes())
        if set(value)!={'nonce'} or not re.fullmatch('[a-f0-9]{32}',value['nonce']):raise WorkflowError('invalid local PG identity')
        return value['nonce']
    value=secrets.token_hex(16)
    atomic_write(path,json.dumps({'nonce':value}).encode());return value


def names(nonce):
    if not re.fullmatch('[a-f0-9]{32}',nonce):raise WorkflowError('invalid resource identity')
    stem='requi-local-'+nonce[:16]
    return {'db':stem+'-pg','data':stem+'-data','socket':stem+'-socket','network':stem+'-network','app':stem+'-app'}


def require_owned(value,nonce,kind,expected):
    labels=(value.get('Config',{}).get('Labels') if kind=='container' else value.get('Labels')) or {}
    actual=value.get('Name','').lstrip('/')
    if labels.get(LABEL)!=nonce or actual!=expected:raise WorkflowError('local resource ownership differs; nothing changed')


def inspect_owned(kind,name,nonce):
    result=subprocess.run(['docker',kind,'inspect',name],capture_output=True,text=True,timeout=30)
    if result.returncode:return None
    values=json.loads(result.stdout)
    if not isinstance(values,list) or len(values)!=1:raise WorkflowError('resource identity unavailable')
    value=values[0];require_owned(value,nonce,kind,name);return value


def controller(resource,nonce,payload):
    value=inspect_owned('container',resource['db'],nonce)
    if not value or value.get('State',{}).get('Running') is not True:raise WorkflowError('owned local PG not running')
    mounts={m['Destination']:m.get('Name') for m in value.get('Mounts',[]) if m.get('Type')=='volume'}
    if mounts!={'/var/lib/postgresql/data':resource['data'],'/owned':resource['socket']}:
        raise WorkflowError('local PG mount identity differs')
    if value.get('HostConfig',{}).get('PortBindings') or value.get('HostConfig',{}).get('NetworkMode')!='none':
        raise WorkflowError('local PG must have no network or TCP publication')
    result=subprocess.run(['docker','exec','--interactive',resource['db'],'python3','/controller/service.py'],
        input=json.dumps(payload),capture_output=True,text=True,timeout=60)
    if result.returncode:raise WorkflowError('owned local PG controller refused; no private output shown')
    return object_json(result.stdout.encode())


def settings(source,nonce,resource,port):
    from image_settings import validate
    original=validate(Path(source).expanduser().resolve())
    # Source may describe production, but NO data/admin address is ever copied,
    # resolved or connected. Ambient environment has no fallback at this boundary.
    result={key:value for key,value in original.items() if key in ALLOWED}
    result.update(DELIVERY_DATABASE_URL=DSN,INTERVIEW_ENGINE_TOKEN=secrets.token_urlsafe(32),
        DELIVERY_PUBLIC_ORIGIN='http://127.0.0.1:'+str(port))
    # Operator config is a local value reviewed at launch, never copied authority.
    from delivery_api.backoffice_access import DEFAULT_CAPABILITIES
    result['REQUI_OPERATOR_REGISTRY_VERSION']='owned-local-v1'
    result['REQUI_OPERATOR_CAPABILITIES']=json.dumps(sorted(DEFAULT_CAPABILITIES|{'sessions.operate'}),separators=(',',':'))
    # Bind only an explicitly reviewed existing operator subject value. Session
    # enrollment must verify it, not promote any account that signs in.
    subject=original.get('REQUI_OPERATOR_SUBJECT')
    if not subject:raise WorkflowError('source settings need reviewed REQUI_OPERATOR_SUBJECT; no guessed operator')
    from uuid import UUID
    result['REQUI_OPERATOR_SUBJECT']=str(UUID(subject))
    if any('\n' in value or '\r' in value for value in result.values()):raise WorkflowError('single-line settings values required')
    if any(key in result for key in DB_KEYS-{'DELIVERY_DATABASE_URL'}):raise WorkflowError('external database setting refused')
    if result['DELIVERY_DATABASE_URL']!=DSN:raise WorkflowError('only owned Unix socket database allowed')
    return result


def build_dependency(root,nonce):
    import tempfile,shutil
    tag='requi-local-pg:'+nonce[:16]
    paths=['scripts/local-pg/Dockerfile','scripts/local-pg/service.py','tests/pg_harness.py','src/delivery_api/runtime_roles.py']
    paths += [str(p.relative_to(root)) for p in sorted((root/'supabase/migrations').glob('*.sql'))]
    with tempfile.TemporaryDirectory(prefix='requi-local-pg-build-') as tmp:
        context=Path(tmp)
        for path in paths:
            target=context/path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/path,target);target.chmod(0o644)
        command(['docker','build','--file',str(context/'scripts/local-pg/Dockerfile'),'--tag',tag,str(context)],timeout=1800)
    return tag


def ensure_dependency(root,resource,nonce):
    # No caller DB URL, Docker network or PG port parameter exists.
    for kind,key in [('volume','data'),('volume','socket')]:
        if inspect_owned(kind,resource[key],nonce) is None:
            command(['docker','volume','create','--label',LABEL+'='+nonce,resource[key]])
    existing=inspect_owned('container',resource['db'],nonce)
    if existing:
        if existing.get('State',{}).get('Running') is not True:command(['docker','start',resource['db']])
    else:
        tag=build_dependency(root,nonce)
        command(['docker','run','--detach','--name',resource['db'],'--label',LABEL+'='+nonce,
            '--network','none','--memory','512m','--pids-limit','128','--env','REQUI_RESOURCE_NONCE='+nonce,
            '--mount','type=volume,source='+resource['data']+',target=/var/lib/postgresql/data',
            '--mount','type=volume,source='+resource['socket']+',target=/owned',tag])
    for _ in range(120):
        try:
            result=controller(resource,nonce,{'action':'check'})
            if result=={'ready':True,'nonce':nonce,'tcp':False}:return
        except WorkflowError:pass
        time.sleep(1)
    raise WorkflowError('local PostgreSQL readiness timed out; inspect owned dependency log, do not use a remote DSN')


def accepted_image(state):
    accepted=state.state['stages'].get('local-acceptance',{});built=state.state['stages'].get('build',{})
    image=built.get('image_id')
    if (not image or accepted.get('image_id')!=image or accepted.get('runtime_contract')!=state.inputs['runtime_contract'] or
        accepted.get('checks')!={name:'passed' for name in ('startup','signed-http','child-lifecycle','stop','retained-outputs')}):
        raise WorkflowError('exact current local image acceptance required; run req image --accept-local')
    if inspect_image(image)['Id']!=image:raise WorkflowError('accepted image identity differs')
    return image


def start_app(resource,nonce,image,envfile,port):
    from image_settings import validate
    values=validate(envfile)
    if values.get('DELIVERY_DATABASE_URL')!=DSN or any(values.get(k) for k in DB_KEYS-{'DELIVERY_DATABASE_URL'}):
        raise WorkflowError('only owned local socket DSN allowed')
    controller(resource,nonce,{'action':'check'})
    if inspect_owned('network',resource['network'],nonce) is None:
        command(['docker','network','create','--label',LABEL+'='+nonce,resource['network']])
    old=inspect_owned('container',resource['app'],nonce)
    if old:raise WorkflowError('owned local app already exists; use explicit stop before restarting; persistent PG data is retained')
    args=Target(image,resource['app'],str(envfile.resolve()),port,resource['network']).arguments()
    args[1:1]=['--label',LABEL+'='+nonce,'--mount','type=volume,source='+resource['socket']+',target=/owned,readonly']
    command(['docker',*args])
    for _ in range(120):
        live=inspect_owned('container',resource['app'],nonce)
        if live and live.get('Image')==image and live.get('State',{}).get('Health',{}).get('Status')=='healthy':return
        if live and live.get('State',{}).get('Running') is not True:break
        time.sleep(1)
    raise WorkflowError('local app readiness unconfirmed; owned containers retained, no automatic retry')


def stop(resource,nonce):
    # Revoke BEFORE app stop so copied bearer tokens do not outlive the controller.
    controller(resource,nonce,{'action':'revoke_all'})
    app=inspect_owned('container',resource['app'],nonce)
    if app:
        command(['docker','stop','--time','30',resource['app']]);command(['docker','rm',resource['app']])
    db=inspect_owned('container',resource['db'],nonce)
    if db:command(['docker','stop','--time','30',resource['db']])


def preview(*,root,state,source,home,port=8002,backoffice_port=8021,apply=False,action='start'):
    home=Path(home).expanduser();home.mkdir(parents=True,exist_ok=True,mode=0o700)
    if home.is_symlink() or home.stat().st_mode&0o077:raise WorkflowError('private local PG workflow directory required')
    nonce=identity(home/'identity.json');resource=names(nonce)
    if action in ('stop','reset'):
        if not apply:log('INFO',action+' review only; owned persistent local PG data untouched');return
        if not confirm('stop owned local preview'+(' and DELETE its local task/accounting data permanently; this never refunds provider charges' if action=='reset' else '; keep persistent local data')):raise WorkflowError('declined')
        if action=='reset':
            review=controller(resource,nonce,{'action':'reset_check'})
            if not review['reset_allowed']:raise WorkflowError('Local paid-attempt evidence exists; destructive reset refused. Preserve accounting and review an archive, never clear costs to obtain headroom.')
        stop(resource,nonce)
        if action=='reset':
            db=inspect_owned('container',resource['db'],nonce)
            if db:command(['docker','rm',resource['db']])
            for key in ('data','socket'):
                if inspect_owned('volume',resource[key],nonce):command(['docker','volume','rm',resource[key]])
        return
    image=accepted_image(state);values=settings(source,nonce,resource,port)
    log('INFO','local PG review: SAME accepted app, persistent owned Unix-socket database, real hosted Auth HTTPS. No Supabase DATA connection, no paid Start, no real payments.')
    log('WARN','Local accounting is separate from hosted accounting. Reset cannot undo provider charges. Model fit and hosted pooler/revocation parity are NOT proved.')
    if not apply:return
    log('WARN','Initial local setup grants 100 LOCAL test credits once, local reviewed-operator workspace access, and unchanged $2/$3/$4 guard values. No hosted balance or access is changed. Review model pins in Back office before Start. A copied explicit provider key permits later owner-started paid work only; no call starts now.')
    if not confirm('prepare owned persistent local PG, enroll the reviewed operator locally and open real hosted sign-in; no task starts'):raise WorkflowError('declined')
    envfile=home/'local.env';atomic_write(envfile,(''.join(k+'='+v+'\n' for k,v in values.items())).encode())
    ensure_dependency(root,resource,nonce)
    start_app(resource,nonce,image,envfile,port)
    from private_backoffice import PrivateBackoffice
    from local_pg_signin import browse
    backoffice=PrivateBackoffice(backoffice_port,port,home/'backoffice.log')
    try:
        backoffice.start()
        log('PASS','local app http://127.0.0.1:'+str(port)+'; private Back office http://127.0.0.1:'+str(backoffice_port))
        browse('http://127.0.0.1:'+str(port),values,lambda payload:controller(resource,nonce,payload))
    finally:
        backoffice.close()
        try:controller(resource,nonce,{'action':'revoke_all'})
        except WorkflowError:log('WARN','Session cleanup unconfirmed. Local session expiry cleanup remains active in owned PG; inspect before reopening.')
