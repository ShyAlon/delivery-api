"""Owner-invoked remote exact-image apply. Program JSON arrives over SSH stdin.

No builds, source checkout, schema changes, public proxy edits or unrelated
cleanup. This file is not executed during implementation tests.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def call(args,*,secret=None,timeout=120):
    result=subprocess.run(args,input=secret,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('remote container operation failed; private output omitted')
    return result.stdout.strip()


def apply(plan):
    required={'reference','config_digest','name','env_file','port','network','username','token','runtime_arguments'}
    if set(plan)!=required:raise ValueError('remote apply schema differs')
    reference=plan['reference'];image_id=plan['config_digest'];name=plan['name']
    if not re.fullmatch(r'ghcr\.io/[a-z0-9_.-]+/[a-z0-9_./-]+@sha256:[0-9a-f]{64}',reference):raise ValueError('immutable GHCR reference required')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id):raise ValueError('config digest required')
    if not re.fullmatch(r'requi-[A-Za-z0-9_.-]{1,80}',name):raise ValueError('owned service name required')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,38}',plan['username']):raise ValueError('registry username required')
    envfile=Path(plan['env_file'])
    if not envfile.is_absolute() or not envfile.is_file() or envfile.stat().st_mode&0o077:
        raise ValueError('private existing remote settings file required')
    if type(plan['token']) is not str or len(plan['token'])<10:raise ValueError('read-only registry token required')
    # Target is the same runtime_spec.py class prepended by the owner runner.
    args=plan['runtime_arguments']
    expected=Target(image_id,name,str(envfile),plan['port'],plan['network']).arguments()
    if args!=expected:raise ValueError('remote runtime differs from the shared specification')
    # A temporary Docker login never persists in the deploy user's real config.
    with tempfile.TemporaryDirectory(prefix='requi-registry-') as config:
        docker=['sudo','-n','docker','--config',config]
        call(['docker','--config',config,'login','ghcr.io','--username',plan['username'],'--password-stdin'],secret=plan['token'])
        plan['token']=''
        call([*docker,'pull','--platform','linux/amd64',reference],timeout=1800)
        image=json.loads(call([*docker,'image','inspect','--format','{{json .}}',reference]))
        if image.get('Id')!=image_id or image.get('Os')!='linux' or image.get('Architecture')!='amd64' or reference not in image.get('RepoDigests',[]):
            raise ValueError('pulled image differs; current service untouched')
    # No current container is stopped until identity and settings both passed.
    docker=['sudo','-n','docker']
    previous=None
    found=subprocess.run([*docker,'container','inspect','--format','{{json .}}',name],capture_output=True,text=True,timeout=60)
    if found.returncode==0:
        current=json.loads(found.stdout)
        # Refuse to hijack a same-name container outside this workflow.
        if (current.get('Config',{}).get('Labels') or {}).get('requi.shared-runtime')!='1':
            raise ValueError('current container ownership unverified; nothing stopped')
        previous=name+'-rollback-'+str(time.time_ns())
        call([*docker,'stop','--time','30',name])
        try:call([*docker,'rename',name,previous])
        except BaseException:
            call([*docker,'start',name])
            raise RuntimeError('rollback naming failed; original service restart requested') from None
    started=False
    try:
        launch=list(args);launch[1:1]=['--label','requi.shared-runtime=1']
        # Docker can create the named container even when run exits nonzero.
        # Mark the attempt before calling so rollback checks and removes it.
        started=True
        call([*docker,*launch])
        for _ in range(120):
            live=json.loads(call([*docker,'container','inspect','--format','{{json .}}',name]))
            if live.get('Image')!=image_id:raise ValueError('running image identity differs')
            state=live.get('State',{})
            if state.get('Health',{}).get('Status')=='healthy':
                return {'state':'healthy','config_digest':image_id,'reference':reference,'rollback_container':previous}
            if state.get('Running') is not True or state.get('Health',{}).get('Status')=='unhealthy':break
            time.sleep(1)
        raise RuntimeError('candidate readiness did not pass')
    except BaseException:
        if started:
            candidate=subprocess.run([*docker,'container','inspect','--format','{{json .}}',name],capture_output=True,text=True,timeout=60)
            if candidate.returncode==0:
                found=json.loads(candidate.stdout)
                if found.get('Image')!=image_id or (found.get('Config',{}).get('Labels') or {}).get('requi.shared-runtime')!='1':
                    raise RuntimeError('candidate cleanup ownership differs; manual recovery required') from None
                call([*docker,'rm','--force',name])
        if previous:
            call([*docker,'rename',previous,name]);call([*docker,'start',name])
            restored=json.loads(call([*docker,'container','inspect','--format','{{json .}}',name]))
            if restored.get('State',{}).get('Running') is not True:raise RuntimeError('rollback restart unconfirmed') from None
        raise RuntimeError('candidate failed; previous restart requested and checked, health must be reviewed') from None


if __name__=='__main__':
    try:
        raw=sys.stdin.buffer.read(262145)
        if len(raw)>262144:raise ValueError('bounded remote plan required')
        result=apply(json.loads(raw));print(json.dumps(result,sort_keys=True))
    except Exception as error:
        print(json.dumps({'state':'failed','error_class':type(error).__name__,'detail':str(error) if isinstance(error,(RuntimeError,ValueError)) else 'remote apply failed; private details omitted'}))
        raise SystemExit(2)
