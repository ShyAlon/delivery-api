#!/usr/bin/env python3
"""Private PG service-file backup; dry by default. No live/default source.

Custom archive includes data/schema, not global roles, Auth identity authority,
object storage or filesystem artifacts. No restore mutation in this helper.
"""
import argparse,json,os,re,stat,subprocess,time
from pathlib import Path

from ops_common import digest,new_private_directory,regular,write_manifest


def clean_environment(service_file=None,service=None):
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','SYSTEMROOT')}
    if service_file:
        service_file=regular(service_file)
        if stat.S_IMODE(service_file.stat().st_mode)&0o077:raise ValueError('private PG service file required (chmod600)')
        if not isinstance(service,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',service):raise ValueError('explicit bounded PG service name required')
        env.update(PGSERVICEFILE=str(service_file.resolve()),PGSERVICE=service,PGCONNECT_TIMEOUT='10')
    return env


def run(command,env):
    try:result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=300)
    except (OSError,subprocess.TimeoutExpired) as exc:raise RuntimeError('PostgreSQL backup utility unavailable/timed out; no success recorded') from exc
    if result.returncode:raise RuntimeError('PostgreSQL backup utility failed; no success recorded (raw credential-bearing errors omitted)')


def snapshot(service_file,service,output):
    env=clean_environment(service_file,service);output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('new archive directory required; no overwrite')
    output=new_private_directory(output);archive=output/'database.dump'
    fd=os.open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    run(['pg_dump','--format=custom','--no-password','--file',str(archive)],env)
    run(['pg_restore','--list',str(archive)],clean_environment())
    manifest={'version':1,'format':'pg-custom-archive','file':'database.dump','bytes':archive.stat().st_size,
        'sha256':digest(archive),'created_at':time.time(),'archive_list_verified':True,'restore_drill_verified':False,
        'coverage_exclusions':['global roles','object storage','filesystem artifacts','managed Auth service/config']}
    write_manifest(output/'manifest.json',manifest)
    return manifest


def verify(bundle):
    bundle=Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir():raise ValueError('regular archive directory required')
    m=json.loads(regular(bundle/'manifest.json').read_text());archive=regular(bundle/'database.dump')
    if m.get('version')!=1 or m.get('format')!='pg-custom-archive' or m.get('file')!='database.dump':raise ValueError('fixed archive manifest required')
    if m.get('sha256')!=digest(archive) or m.get('bytes')!=archive.stat().st_size:raise ValueError('archive digest/size mismatch')
    run(['pg_restore','--list',str(archive)],clean_environment())
    return {'archive_integrity_verified':True,'restore_drill_verified':False,'launch_ready':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('snapshot');s.add_argument('--service-file',required=True);s.add_argument('--service',required=True);s.add_argument('--output',required=True);s.add_argument('--execute',action='store_true')
    v=sub.add_parser('verify');v.add_argument('--bundle',required=True)
    a=p.parse_args()
    if a.action=='snapshot' and not a.execute:print(json.dumps({'dry_run':True,'effect_executed':False}));return
    result=snapshot(a.service_file,a.service,a.output) if a.action=='snapshot' else verify(a.bundle)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
