import json
from types import SimpleNamespace
import pytest
from delivery_api.producer_inputs import read_fresh_inputs,MAX_DOCUMENT_BYTES
from delivery_api.tenancy import TenantScope,session_root,TenantAccessDenied


def setup(tmp_path):
    cap=SimpleNamespace(scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001'),session_id='session',validate=lambda store:None)
    root=session_root(tmp_path,cap.scope,cap.session_id);(root/'interview').mkdir(parents=True)
    (root/'spec').mkdir()
    paths=[root/'interview'/'intent_model.json',root/'interview'/'conops.json',root/'spec'/'system-baseline.json']
    for path in paths:path.write_text(json.dumps({'source':path.name}))
    return cap,root,paths


def test_reads_only_existing_canonical_documents_without_writes(tmp_path):
    cap,root,paths=setup(tmp_path);before=[p.read_bytes() for p in paths]
    result=read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)
    assert set(result)=={'intent','conops','baseline'} and before==[p.read_bytes() for p in paths]
    assert not (root/'spec'/'checkpoints.sqlite').exists()


@pytest.mark.parametrize('unsafe',['root','symlink','oversize','array','missing'])
def test_input_reader_rejects_unsafe_or_unresolved_source(tmp_path,unsafe):
    cap,root,paths=setup(tmp_path)
    if unsafe=='root':root=tmp_path
    elif unsafe=='symlink':paths[0].unlink();paths[0].symlink_to(paths[1])
    elif unsafe=='oversize':paths[0].write_text(' '* (MAX_DOCUMENT_BYTES+1))
    elif unsafe=='array':paths[0].write_text('[]')
    elif unsafe=='missing':paths[0].unlink()
    with pytest.raises((ValueError,TenantAccessDenied,FileNotFoundError)):
        read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)
    assert not (root/'spec'/'checkpoints.sqlite').exists()


def test_parent_symlink_swap_after_validation_cannot_read_foreign_document(tmp_path,monkeypatch):
    import os
    from delivery_api import producer_inputs as module
    cap,root,paths=setup(tmp_path);foreign=tmp_path/'foreign';foreign.mkdir()
    (foreign/'intent_model.json').write_text('{"private":"foreign"}')
    original=os.open;swapped=[]
    def replace(path,flags,*args,**kwargs):
        if path=='intent_model.json' and not swapped:
            (root/'interview').rename(root/'old-interview');(root/'interview').symlink_to(foreign,target_is_directory=True);swapped.append(True)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(module.os,'open',replace)
    with pytest.raises(TenantAccessDenied):read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)
    assert swapped


def test_session_directory_replacement_is_rejected_after_pinned_read(tmp_path,monkeypatch):
    import os
    from delivery_api import producer_inputs as module
    cap,root,paths=setup(tmp_path);original=os.open;swapped=[]
    def replace(path,flags,*args,**kwargs):
        if path=='intent_model.json' and not swapped:
            root.rename(root.with_name('old-session'));root.mkdir();swapped.append(True)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(module.os,'open',replace)
    with pytest.raises(TenantAccessDenied):read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)
    assert swapped


def test_final_file_swap_uses_pinned_fd_but_rejects_changed_identity(tmp_path,monkeypatch):
    import os
    from delivery_api import producer_inputs as module
    cap,root,paths=setup(tmp_path);original=os.fstat;swapped=[]
    def replace(fd):
        info=original(fd)
        if info.st_size==paths[0].stat().st_size and not swapped:
            paths[0].rename(paths[0].with_suffix('.old'));paths[0].write_text('{"changed":true}');swapped.append(True)
        return info
    monkeypatch.setattr(module.os,'fstat',replace)
    with pytest.raises(TenantAccessDenied):read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)


@pytest.mark.parametrize('when',['during_read','later_document'])
def test_same_inode_content_change_cannot_supply_mixed_input_snapshot(tmp_path,monkeypatch,when):
    import os
    from delivery_api import producer_inputs as module
    cap,root,paths=setup(tmp_path);before_inode=paths[0].stat().st_ino;changed=[]
    original_open=os.open;original_fdopen=os.fdopen
    def mutate():
        with paths[0].open('r+b') as file:file.write(b'{"mutate":"same-inode"}');file.truncate()
        assert paths[0].stat().st_ino==before_inode
        changed.append(True)
    if when=='during_read':
        class Reader:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def read(self,size):
                answer=self.stream.read(size)
                if not changed:mutate()
                return answer
        monkeypatch.setattr(module.os,'fdopen',lambda *args,**kwargs:Reader(original_fdopen(*args,**kwargs)))
    else:
        def open_later(path,flags,*args,**kwargs):
            if path=='conops.json' and not changed:mutate()
            return original_open(path,flags,*args,**kwargs)
        monkeypatch.setattr(module.os,'open',open_later)
    with pytest.raises(TenantAccessDenied,match='content changed|snapshot read'):
        read_fresh_inputs(None,cap,root,artifacts_base=tmp_path)
    assert changed and not (root/'spec'/'checkpoints.sqlite').exists()
