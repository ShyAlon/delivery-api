#!/usr/bin/env python3
"""Offline backup/drill helper. No live/default database, destructive restore or upload.

SQLite legacy/local file only. Cloud PostgreSQL uses reviewed pg_dump/snapshot
procedures; a local SQLite drill never claims Supabase backup completeness.
"""
import argparse,hashlib,json,os,sqlite3,time
from pathlib import Path


def regular(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('regular non-symlink file required')
    return path


def new_private_directory(path):
    path=Path(path).absolute()
    if any(parent.is_symlink() for parent in (path,*path.parents)):
        raise ValueError('backup/drill paths must not traverse symlinks')
    if path.exists():raise ValueError('new private directory required; no overwrite')
    path.mkdir(mode=0o700)
    return path


def write_manifest(path,manifest):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as handle:json.dump(manifest,handle,sort_keys=True)


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def integrity(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('SQLite integrity failed')
        if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SQLite foreign key check failed')


def backup(source,output):
    source=regular(source);output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('new backup directory required; no overwrite')
    output=new_private_directory(output)
    target=output/'database.sqlite'
    try:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dest:
            src.backup(dest)
        integrity(target)
        manifest={'version':1,'format':'sqlite-local-snapshot','created_at':time.time(),
            'file':'database.sqlite','sha256':digest(target),'bytes':target.stat().st_size,
            'integrity':'verified','cloud_backup':False,'artifacts_included':False}
        path=output/'manifest.json'
        write_manifest(path,manifest)
        return manifest
    except Exception:
        # Leave failed evidence for operator review, never report a success.
        raise


def verify(bundle):
    bundle=Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir():raise ValueError('regular backup directory required')
    manifest=json.loads(regular(bundle/'manifest.json').read_text())
    if manifest.get('version')!=1 or manifest.get('format')!='sqlite-local-snapshot' or manifest.get('file')!='database.sqlite':
        raise ValueError('supported fixed backup manifest required')
    target=regular(bundle/'database.sqlite')
    if manifest.get('sha256')!=digest(target) or manifest.get('bytes')!=target.stat().st_size:
        raise ValueError('backup digest or size mismatch')
    integrity(target)
    return manifest


def restore_drill(bundle,output):
    verify(bundle);output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('new drill directory required; never restore over a database')
    output=new_private_directory(output)
    dest=output/'drill.sqlite';fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with sqlite3.connect((Path(bundle)/'database.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(dest) as target:
        src.backup(target)
    integrity(dest)
    return {'restored_to_new_scratch':True,'integrity':'verified','live_restore':False,'cloud_restore':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    b=sub.add_parser('snapshot');b.add_argument('--source',required=True);b.add_argument('--output',required=True);b.add_argument('--execute',action='store_true')
    v=sub.add_parser('verify');v.add_argument('--bundle',required=True)
    d=sub.add_parser('drill');d.add_argument('--bundle',required=True);d.add_argument('--output',required=True);d.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.action!='verify' and not args.execute:
        print(json.dumps({'dry_run':True,'action':args.action,'effect_executed':False}));return
    if args.action=='snapshot':result=backup(args.source,args.output)
    elif args.action=='verify':result=verify(args.bundle)
    else:result=restore_drill(args.bundle,args.output)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
