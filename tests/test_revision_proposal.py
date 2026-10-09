import hashlib,json,os
from types import SimpleNamespace
import pytest
from delivery_api.tenancy import TenantScope,session_root,TenantAccessDenied
from delivery_api.revision_proposal import read_retained_revision_proposal
from test_revision_intent import proposal
from test_closed_revision_job import options


def setup(tmp_path):
    scope=TenantScope('00000000-0000-0000-0000-000000000001')
    cap=SimpleNamespace(scope=scope,session_id='s',task_id='task',generation=1,validate=lambda store:None)
    root=session_root(tmp_path,scope,'s');pid='a'*32
    path=root/'spec'/'revision-proposals'/pid/'proposal.json';path.parent.mkdir(parents=True)
    body=proposal()|{'proposal_id':pid};path.write_text(json.dumps(body))
    def read():return read_retained_revision_proposal(None,cap,root,pid,artifacts_base=tmp_path,options=options())
    return cap,root,path,body,read


def test_exact_pinned_proposal_and_raw_digest(tmp_path):
    cap,root,path,body,read=setup(tmp_path);before=path.read_bytes()
    assert read()=={'proposal':body,'document_sha256':hashlib.sha256(before).hexdigest()}
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',[{'session_id':'foreign'},{'task_id':'foreign'},{'generation':2},
    {'proposal_id':'b'*32},{'artifact':{'changed':True}},{'committed':True},
    {'source_checkpoint_id':'changed'},{'change_request':'changed'}])
def test_rejects_misbound_or_changed_retained_proposal(tmp_path,change):
    cap,root,path,body,read=setup(tmp_path);path.write_text(json.dumps(body|change))
    with pytest.raises((TenantAccessDenied,ValueError)):read()


@pytest.mark.parametrize('unsafe',['file-symlink','directory-symlink','foreign-root','malformed-id','directory-file'])
def test_unsafe_namespace_is_rejected(tmp_path,unsafe):
    cap,root,path,body,read=setup(tmp_path)
    if unsafe=='file-symlink':path.rename(tmp_path/'outside');path.symlink_to(tmp_path/'outside')
    elif unsafe=='directory-symlink':path.parent.rename(tmp_path/'outside');path.parent.symlink_to(tmp_path/'outside',target_is_directory=True)
    elif unsafe=='directory-file':path.unlink();path.mkdir()
    elif unsafe=='foreign-root':
        read=lambda:read_retained_revision_proposal(None,cap,tmp_path,'a'*32,artifacts_base=tmp_path,options=options())
    else:
        read=lambda:read_retained_revision_proposal(None,cap,root,'../outside',artifacts_base=tmp_path,options=options())
    with pytest.raises((OSError,ValueError,TenantAccessDenied)):read()


def test_directory_swap_is_rejected(tmp_path,monkeypatch):
    from delivery_api import revision_proposal as module
    cap,root,path,body,read=setup(tmp_path);original=os.open;changed=[]
    def opened(name,flags,*args,**kwargs):
        fd=original(name,flags,*args,**kwargs)
        if name=='proposal.json' and not changed:
            path.parent.rename(root/'spec'/'old-proposal');path.parent.mkdir();changed.append(True)
        return fd
    monkeypatch.setattr(module.os,'open',opened)
    with pytest.raises((TenantAccessDenied,FileNotFoundError)):read()


def test_revoked_capability_on_final_check_is_rejected(tmp_path):
    cap,root,path,body,read=setup(tmp_path);calls=[]
    def authority(store):
        calls.append(1)
        if len(calls)>1:raise TenantAccessDenied('revoked')
    cap.validate=authority
    with pytest.raises(TenantAccessDenied,match='revoked'):read()
