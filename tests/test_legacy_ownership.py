import sqlite3
import pytest
from delivery_api.legacy_ownership import plan_legacy_import
from delivery_api.tenancy import TenantAccessDenied
A='00000000-0000-0000-0000-000000000001'
B='00000000-0000-0000-0000-000000000002'

@pytest.fixture
def legacy(tmp_path):
    db=tmp_path/'legacy.db'
    with sqlite3.connect(db) as connection:
        connection.executescript("CREATE TABLE tasks(task_id text,owner_id text,engine_session_id text); CREATE TABLE runs(run_id text,owner_id text); INSERT INTO tasks VALUES ('t',NULL,'s'),('unowned',NULL,NULL); INSERT INTO runs VALUES ('r',NULL)")
    root=tmp_path/'files';root.mkdir();(root/'s').mkdir();(root/'unbound').mkdir()
    return db,root


def test_no_mapping_means_quarantine_not_first_access_claim(legacy):
    db,root=legacy
    before=db.read_bytes()
    plan=plan_legacy_import(db,root,dict(tasks={},runs={},sessions={}))
    assert plan['quarantine']=={'tasks':['t','unowned'],'runs':['r'],'sessions':['s','unbound']}
    assert db.read_bytes()==before


def test_explicit_mapping_only_and_digest(legacy):
    db,root=legacy
    plan=plan_legacy_import(db,root,dict(tasks={'t':A},runs={'r':A},sessions={'s':A}))
    assert plan['session_tasks']=={'s':'t'}
    assert plan['quarantine']['tasks']==['unowned']
    assert len(plan['mapping_digest'])==64


@pytest.mark.parametrize('mapping',[
 dict(tasks={'t':A},runs={},sessions={'s':B}),
 dict(tasks={},runs={},sessions={'s':A}),
 dict(tasks={'unknown':A},runs={},sessions={}),
 dict(tasks={'t':'local-admin'},runs={},sessions={'s':A})])
def test_conflicting_unknown_or_unbound_map_rejected(legacy,mapping):
    with pytest.raises((ValueError,TenantAccessDenied)):plan_legacy_import(*legacy,mapping)


def test_escaped_legacy_file_rejected(legacy):
    db,root=legacy;(root/'s'/'escape').symlink_to('/tmp')
    with pytest.raises(TenantAccessDenied):plan_legacy_import(db,root,dict(tasks={'t':A},runs={},sessions={'s':A}))
