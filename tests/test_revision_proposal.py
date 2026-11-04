"""Retained proposal identity and content use PG, never host files."""
import hashlib,json
from uuid import uuid4
import pytest
from delivery_api.tenancy import session_root,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability
from delivery_api.revision_proposal import read_retained_revision_proposal
from test_revision_intent import proposal
from test_closed_revision_job import options


def setup(tmp_path,workspace,change=None):
    store=workspace.store;tid=store.create_task('Retained proposal')['task_id']
    sid='proposal-'+uuid4().hex;store.set_engine_session(tid,sid);cap=WorkerCapability.capture(store,tid);pid='a'*32
    body=proposal()|{'proposal_id':pid,'task_id':tid,'session_id':sid,'generation':cap.generation}|(change or {})
    store.retain_decision_review(tid,'pg-revision-proposal:'+pid,'result',body)
    root=session_root(tmp_path,cap.scope,cap.session_id)
    def read():return read_retained_revision_proposal(store,cap,root,pid,artifacts_base=tmp_path,options=options())
    return store,cap,root,body,read


def test_exact_retained_proposal_survives_host_folder_loss(tmp_path,pg_scoped_workspace):
    store,cap,root,body,read=setup(tmp_path,pg_scoped_workspace)
    expected={'proposal':body,'document_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,indent=2).encode()).hexdigest()}
    assert not root.exists() and read()==expected
    root.mkdir(parents=True);(root/'spec').symlink_to(tmp_path/'foreign')
    assert read()==expected


@pytest.mark.parametrize('change',[{'session_id':'foreign'},{'task_id':'foreign'},{'generation':2},
    {'proposal_id':'b'*32},{'artifact':{'changed':True}},{'committed':True},
    {'source_checkpoint_id':'changed'},{'change_request':'changed'}])
def test_rejects_misbound_or_changed_retained_proposal(tmp_path,pg_scoped_workspace,change):
    *_,read=setup(tmp_path,pg_scoped_workspace,change)
    with pytest.raises((TenantAccessDenied,ValueError)):read()


def test_unsafe_identity_or_root_refused(tmp_path,pg_scoped_workspace):
    store,cap,root,body,read=setup(tmp_path,pg_scoped_workspace)
    with pytest.raises(TenantAccessDenied):read_retained_revision_proposal(store,cap,tmp_path,'a'*32,artifacts_base=tmp_path,options=options())
    with pytest.raises(ValueError):read_retained_revision_proposal(store,cap,root,'../outside',artifacts_base=tmp_path,options=options())


def test_stale_capability_refused(tmp_path,pg_scoped_workspace):
    store,cap,root,body,read=setup(tmp_path,pg_scoped_workspace)
    with store._task_operation_guard('task.rerun'):
        store._execute_native('UPDATE tasks SET generation=generation+1 WHERE task_id=%s',(cap.task_id,),write=True)
    with pytest.raises(TenantAccessDenied):read()
