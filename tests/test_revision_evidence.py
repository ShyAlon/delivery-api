from types import SimpleNamespace
import hashlib,os
import pytest
from delivery_api.tenancy import TenantScope,session_root,TenantAccessDenied
from delivery_api.revision_evidence import read_revision_artifacts


def setup(tmp_path):
    scope=TenantScope('00000000-0000-0000-0000-000000000001')
    cap=SimpleNamespace(scope=scope,session_id='s',generation=2,validate=lambda store:None)
    root=session_root(tmp_path,scope,'s');(root/'spec').mkdir(parents=True)
    path=root/'spec'/'srs.json';path.write_text('{"fake":"evidence"}')
    return cap,root,path


def test_reads_only_canonical_owned_bytes_without_mutation(tmp_path):
    cap,root,path=setup(tmp_path);before=path.read_bytes()
    evidence=read_revision_artifacts(None,cap,root,'srs',artifacts_base=tmp_path)
    assert evidence==[{'phase':'srs','name':'srs.json','generation':2,'sha256':hashlib.sha256(before).hexdigest()}]
    assert path.read_bytes()==before


@pytest.mark.parametrize('unsafe',['symlink','foreign','missing','directory'])
def test_rejects_wrong_namespace_or_nonregular_artifact(tmp_path,unsafe):
    cap,root,path=setup(tmp_path)
    if unsafe=='symlink':path.unlink();path.symlink_to(tmp_path/'outside')
    elif unsafe=='foreign':root=tmp_path
    elif unsafe=='missing':path.unlink()
    else:path.unlink();path.mkdir()
    with pytest.raises((ValueError,PermissionError,OSError)):
        read_revision_artifacts(None,cap,root,'srs',artifacts_base=tmp_path)


def test_same_inode_mutation_during_read_is_rejected(tmp_path,monkeypatch):
    from delivery_api import revision_evidence as module
    cap,root,path=setup(tmp_path);original=os.fdopen
    class Reader:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close()
        def read(self,size):
            data=self.stream.read(size)
            with path.open('r+b') as f:f.write(b'{"changed":true}');f.truncate()
            return data
    monkeypatch.setattr(module.os,'fdopen',lambda *args,**kwargs:Reader(original(*args,**kwargs)))
    with pytest.raises(TenantAccessDenied,match='changed during'):
        read_revision_artifacts(None,cap,root,'srs',artifacts_base=tmp_path)


def test_parent_directory_swap_during_read_is_rejected(tmp_path,monkeypatch):
    from delivery_api import revision_evidence as module
    cap,root,path=setup(tmp_path);original=os.open;swapped=[]
    def open_file(name,flags,*args,**kwargs):
        fd=original(name,flags,*args,**kwargs)
        if name=='srs.json' and not swapped:
            (root/'spec').rename(root/'old-spec');(root/'spec').mkdir();swapped.append(True)
        return fd
    monkeypatch.setattr(module.os,'open',open_file)
    with pytest.raises(TenantAccessDenied,match='namespace changed'):
        read_revision_artifacts(None,cap,root,'srs',artifacts_base=tmp_path)
