import json
import os
import sqlite3
from pathlib import Path
import uuid
import pytest
import psycopg
from delivery_api.store import SQLiteStore
from delivery_api.legacy_ownership import plan_legacy_import
from delivery_api.legacy_import import apply_legacy_import

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
if DSN and 'dbname=test_tenant_' not in DSN:raise RuntimeError('isolated test DB required')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
A='00000000-0000-0000-0000-000000000001'

@pytest.fixture
def source(tmp_path):
    db=tmp_path/'source.db';store=SQLiteStore(str(db))
    task=store.create_task('import-'+uuid.uuid4().hex)
    sid=str(uuid.uuid4());store.set_engine_session(task['task_id'],sid)
    store.add_event(task['task_id'],'private','saved source')
    root=tmp_path/'source';path=root/sid/'interview';path.mkdir(parents=True)
    (path/'intent_model.json').write_text('{"problem_statement":"private original"}')
    checkpoint=path/'checkpoints.sqlite'
    with sqlite3.connect(checkpoint) as conn:
        conn.execute('CREATE TABLE checkpoint(value text)');conn.execute("INSERT INTO checkpoint VALUES ('private checkpoint')")
    mapping=dict(tasks={task['task_id']:A},runs={},sessions={sid:A})
    return db,root,mapping,task['task_id'],sid


def test_apply_preserves_source_and_coherent_checkpoint(source,tmp_path):
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    before=db.read_bytes()
    with psycopg.connect(DSN) as conn:
        # Dedicated tests run in isolation; preserve legacy event IDs only on an
        # empty target as the import contract requires.
        conn.execute('TRUNCATE tasks,runs,audit_log CASCADE');conn.commit()
        result=apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
              reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn)
        assert result['state']=='committed'
        assert conn.execute('SELECT owner_id::text FROM tasks WHERE task_id=%s',(task,)).fetchone()[0]==A
    target=tmp_path/'target'/'tenants'/A/'sessions'/sid/'interview'
    assert (target/'intent_model.json').read_bytes()==(root/sid/'interview'/'intent_model.json').read_bytes()
    with sqlite3.connect(target/'checkpoints.sqlite') as copy:
        assert copy.execute('SELECT value FROM checkpoint').fetchone()[0]=='private checkpoint'
    assert db.read_bytes()==before
    assert (tmp_path/'backup'/'control-plane.sqlite').exists()


def test_before_commit_failure_rolls_back_rows_and_published_files(source,tmp_path):
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    def fail(stage):raise RuntimeError('injected failure')
    with psycopg.connect(DSN) as conn:
        conn.execute('TRUNCATE tasks,runs,audit_log CASCADE');conn.commit()
        with pytest.raises(RuntimeError):
            apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
                reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn,failpoint=fail)
        assert conn.execute('SELECT count(*) FROM tasks WHERE task_id=%s',(task,)).fetchone()[0]==0
    assert not (tmp_path/'target'/'tenants'/A/'sessions'/sid).exists()
    assert json.loads((tmp_path/'backup'/'receipt.json').read_text())['state']=='rolled_back'
    assert (root/sid/'interview'/'intent_model.json').exists()


def test_changed_review_mapping_stops_before_backup_or_writes(source,tmp_path):
    db,root,mapping,task,sid=source
    with psycopg.connect(DSN) as conn:
        with pytest.raises(ValueError):
            apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
                               reviewed_digest='wrong',reviewed_source_digest='wrong',connection=conn)
    assert not (tmp_path/'backup').exists()


def test_nonempty_target_never_overwritten(source,tmp_path):
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    with psycopg.connect(DSN) as conn:
        conn.execute('TRUNCATE tasks,runs,audit_log CASCADE')
        conn.execute("INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES ('existing','existing','pending',0,0,%s)",(A,));conn.commit()
        with pytest.raises(ValueError):apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
                  reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn)
        assert conn.execute("SELECT title FROM tasks WHERE task_id='existing'").fetchone()[0] in ('existing',b'existing')
    assert not (tmp_path/'target'/'tenants'/A/'sessions'/sid).exists()


def test_source_edit_after_review_rejected(source,tmp_path):
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    (root/sid/'interview'/'intent_model.json').write_text('{"changed":true}')
    with psycopg.connect(DSN) as conn:
        with pytest.raises(ValueError):apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
              reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn)
    assert not (tmp_path/'backup').exists()


def test_receipt_reconciliation_reads_only(source,tmp_path):
    from delivery_api.legacy_import import reconcile_import
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    with psycopg.connect(DSN) as conn:
        conn.execute('TRUNCATE tasks,runs,audit_log CASCADE');conn.commit()
        result=apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
              reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn)
        reply=reconcile_import(tmp_path/'backup'/'receipt.json',conn,mapping)
        assert reply['state']=='committed_verified'
        assert Path(result['published'][0]).is_dir()


def test_source_change_during_copy_rolls_back_target(source,tmp_path):
    db,root,mapping,task,sid=source
    plan=plan_legacy_import(db,root,mapping)
    def alter(stage):(root/sid/'interview'/'intent_model.json').write_text('{"concurrent":"edit"}')
    with psycopg.connect(DSN) as conn:
        conn.execute('TRUNCATE tasks,runs,audit_log CASCADE');conn.commit()
        with pytest.raises(ValueError):apply_legacy_import(db,root,tmp_path/'target',tmp_path/'backup',mapping,
            reviewed_digest=plan['mapping_digest'],reviewed_source_digest=plan['source_digest'],connection=conn,failpoint=alter)
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
    assert not (tmp_path/'target'/'tenants'/A/'sessions'/sid).exists()
    assert json.loads((tmp_path/'backup'/'receipt.json').read_text())['state']=='rolled_back'
