"""Helpers shared by ops-backup.py and ops-postgres-backup.py.

Regular-file checks, new private directories that never overwrite or traverse
symlinks, create-only manifest writes and streamed SHA-256 digests.
"""
import hashlib,json,os
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
