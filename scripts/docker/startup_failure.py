"""Owner-side bounded, sanitized startup evidence, outside container tmpfs."""
import json
from pathlib import Path
import re
from image_receipt import atomic_write
from image_workflow import command as docker_command,log
from delivery_api.debug_export import redact


def capture(name,folder,values,*,command=None):
    command=command or docker_command
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    folder.chmod(0o700)
    secrets=[secret for key,secret in values.items() if isinstance(secret,str) and secret and re.search(r'key|token|secret|password|dsn|database_url',key,re.I)]
    for key in ('DELIVERY_DATABASE_URL','INTERVIEW_ENGINE_SESSIONS_DSN'):
        try:
            from psycopg.conninfo import conninfo_to_dict
            password=conninfo_to_dict(values.get(key,'' )).get('password')
            if password:secrets.append(password)
        except Exception:pass
    def clean(value,limit=24000):
        if isinstance(value,dict):return {key:clean(part,limit) for key,part in value.items()}
        if isinstance(value,list):return [clean(part,limit) for part in value]
        if not isinstance(value,str):return value
        text=value
        for secret in secrets:text=text.replace(secret,'[REDACTED]')
        text=re.sub(r'(?i)postgres(?:ql)?://[^\s"\']+','[database URL redacted]',text)
        text=redact(text)
        text=''.join(c for c in text if c in '\n\t' or ord(c)>=32)
        return text[-limit:] if limit else text
    evidence={'container':name,'state':{},'logs':'','startup':None,'capture_errors':[]}
    try:
        actual=json.loads(command(['docker','container','inspect','--format','{{json .}}',name],timeout=5))
        state=actual.get('State',{})
        evidence['image']=actual.get('Image')
        evidence['state']={key:state.get(key) for key in ('Status','Running','ExitCode','OOMKilled','Error','StartedAt','FinishedAt')}
        health=state.get('Health',{})
        evidence['health']={'Status':health.get('Status'),'FailingStreak':health.get('FailingStreak'),'Log':health.get('Log',[])[-5:]}
    except Exception:evidence['capture_errors'].append('container state unavailable')
    try:evidence['logs']=clean(command(['docker','logs','--timestamps','--tail','80',name],include_stderr=True,timeout=5))
    except Exception:evidence['capture_errors'].append('container logs unavailable')
    temporary=folder/'startup-capture.tmp'
    if temporary.exists() or temporary.is_symlink():temporary.unlink()
    try:
        command(['docker','cp',name+':/data/requi-startup.json',str(temporary)],timeout=5)
        if temporary.is_symlink() or temporary.stat().st_size>65536:raise ValueError('bounded startup file required')
        stored=json.loads(temporary.read_text())
        if stored.get('version')!=1 or not isinstance(stored.get('stages'),list):raise ValueError('startup format differs')
        if any(not isinstance(row,dict) for row in stored['stages']):raise ValueError('invalid stage record')
        evidence['startup']=stored['stages'][-64:]
        evidence['startup_run_id']=stored.get('run_id')
    except Exception:evidence['capture_errors'].append('persisted startup markers unavailable (older image or file not written)')
    finally:
        if temporary.exists():temporary.unlink()
    report_path=folder/'preflight-capture.tmp'
    if report_path.exists() or report_path.is_symlink():report_path.unlink()
    try:
        command(['docker','cp',name+':/data/requi-preflight.json',str(report_path)],timeout=5)
        if report_path.is_symlink() or report_path.stat().st_size>131072:raise ValueError('bounded report required')
        report=json.loads(report_path.read_text())
        if report.get('status') not in ('pass','warn','fail') or not isinstance(report.get('checks'),list):raise ValueError('report format differs')
        if not evidence.get('startup_run_id') or report.get('startup_run_id')!=evidence['startup_run_id']:raise ValueError('stale report from prior container start')
        evidence['preflight']=report
    except Exception:evidence['capture_errors'].append('persisted preflight report unavailable (older image or check still running)')
    finally:
        if report_path.exists():report_path.unlink()
    state=evidence['state'];logs=evidence['logs']
    if state.get('OOMKilled') is True:cause='container was killed for exceeding its memory limit'
    else:
        roots=[line[line.lower().index('root cause:'):] for line in logs.splitlines() if 'root cause:' in line.lower()]
        if roots:cause=roots[-1]
        elif evidence.get('preflight',{}).get('root_cause'):cause=evidence['preflight']['root_cause']
        elif any(row.get('stage')=='database-prerequisite-refused' and row.get('error_kind')=='timeout' for row in evidence.get('startup') or []):cause='database unreachable (timeout); network/firewall/VPN or database endpoint needs checking'
        elif state.get('Running') is True:cause='startup is still running but health did not pass; the last stage and health probe output below identify what remains unverified'
        elif state.get('ExitCode')==2:cause='startup refused; exact cause is unconfirmed without the completed preflight report'
        else:cause='cause unconfirmed; inspect the captured state, startup stage and logs below'
    evidence['probable_cause']=cause
    evidence=clean(evidence)
    # No environment, complete inspect payload, credentials or command arguments.
    path=folder/'startup-failure.json';atomic_write(path,json.dumps(evidence,sort_keys=True,indent=2).encode())
    # Print the same sanitized evidence; a saved path alone is not a diagnosis.
    log('FAIL','preview startup: '+clean(cause))
    for key in ('state','health','startup','preflight','capture_errors'):
        log('INFO',key+': '+json.dumps(evidence.get(key),sort_keys=True))
    if logs:
        for line in logs.splitlines():log('INFO','container log: '+line)
    else:log('WARN','container produced no main log output captured; last startup markers and health output remain the evidence')
    log('INFO','private startup evidence retained at '+str(path))
    return evidence
