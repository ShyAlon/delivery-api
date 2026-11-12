"""Early bounded startup markers. No app config, DSNs or third-party imports."""
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
from datetime import datetime,timezone

_STARTED=time.monotonic()
_RUN_ID=os.environ.get('REQUI_STARTUP_RUN_ID') or uuid4().hex


def stage(name,**facts):
    global _RUN_ID
    # Entry resets old evidence on container restart. The delivery child inherits
    # this ID, so an old refusal can never explain a new startup.
    if name=='entrypoint':
        _RUN_ID=uuid4().hex;os.environ['REQUI_STARTUP_RUN_ID']=_RUN_ID
    # Only names and numeric/status facts, never arbitrary exception text or env.
    if not re.fullmatch(r'[a-z0-9-]{1,80}',name):return
    row={'stage':name,'run_id':_RUN_ID,'at':datetime.now(timezone.utc).isoformat(),'elapsed_s':round(time.monotonic()-_STARTED,3),'pid':os.getpid()}
    for key,value in facts.items():
        if key in ('status','check','error_kind') and isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):row[key]=value
    print('[INFO] startup '+json.dumps(row,sort_keys=True),file=sys.stderr,flush=True)
    # The full image has a writable /data volume even with read-only rootfs.
    # Not the launcher's ephemeral /tmp. Host launches without this env log only.
    path=os.environ.get('REQUI_STARTUP_EVIDENCE')
    if not path:return
    try:
        target=Path(path)
        if target.is_symlink():raise OSError('unsafe startup evidence path')
        previous=[]
        if name!='entrypoint' and target.is_file() and target.stat().st_size<=65536:
            stored=json.loads(target.read_text())
            if stored.get('version')==1 and stored.get('run_id')==_RUN_ID and isinstance(stored.get('stages'),list):previous=stored['stages'][-63:]
        payload=json.dumps({'version':1,'run_id':_RUN_ID,'stages':previous+[row]},sort_keys=True).encode()
        temporary=target.with_suffix('.tmp')
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(payload)
        os.replace(temporary,target)
    except Exception:
        # Stdout/stderr evidence survives; a failed diagnostic write is not a
        # service refusal and must not hide the actual startup problem.
        print('[WARN] startup evidence file unavailable; stderr markers remain authoritative',file=sys.stderr,flush=True)


def persist_report(report):
    """Write the actual refusal report outside preview's /tmp tmpfs too."""
    path=os.environ.get('REQUI_STARTUP_EVIDENCE')
    if not path:return
    try:
        payload=dict(sanitize(report),startup_run_id=_RUN_ID)
        target=Path(path).with_name('requi-preflight.json')
        if target.is_symlink():raise OSError('unsafe report path')
        raw=json.dumps(payload,sort_keys=True).encode()
        if len(raw)>131072:raise ValueError('startup report exceeds bound')
        temporary=target.with_suffix('.tmp')
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw)
        os.replace(temporary,target)
    except Exception:
        print('[WARN] persisted startup report unavailable; stderr remains authoritative',file=sys.stderr,flush=True)


def sanitize(value,env=None):
    """Retain useful driver causes, never connection URLs or secret values."""
    from .debug_export import redact,SENSITIVE
    env=os.environ if env is None else env
    secrets=[value for key,value in env.items() if SENSITIVE.search(key) and isinstance(value,str) and value]
    for key in ('DELIVERY_DATABASE_URL','INTERVIEW_ENGINE_SESSIONS_DSN'):
        dsn=env.get(key,'')
        if dsn:secrets.append(dsn)
        try:
            from psycopg.conninfo import conninfo_to_dict
            password=conninfo_to_dict(dsn).get('password')
            if password:secrets.append(password)
        except Exception:pass
    def walk(part):
        if isinstance(part,dict):return {key:walk(child) for key,child in part.items()}
        if isinstance(part,list):return [walk(child) for child in part]
        if isinstance(part,str):
            for secret in secrets:part=part.replace(secret,'[REDACTED]')
            return re.sub(r'(?i)postgres(?:ql)?://[^\s"\']+','[database URL redacted]',part)
        return part
    return redact(walk(value))
