"""Operator-only legacy import with SQLite backups and a DB transaction.

Filesystem and PostgreSQL cannot share one atomic commit. Files are prepared in
an unservable staging directory, published while DB rows are still uncommitted,
and removed on rollback. A durable receipt makes an interrupted commit explicit;
never retry an uncertain import automatically. Source data is never removed.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from .legacy_ownership import plan_legacy_import
from .tenancy import TenantScope,session_root,TenantAccessDenied

CHILDREN=('phases','gates','events','progress_ingest','progress_seen','review_comments',
          'decision_reviews','decision_drafts','answer_feedback','task_artifacts','audit_log')
RUN_CHILDREN=('links','usage','artifacts')


def _sqlite_backup(source,target):
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('backup failed integrity check')


def _write_receipt(path,payload):
    temp=path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(payload,stream,sort_keys=True,indent=2);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)
    fd=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _copy_session(source,dest):
    # Read-only SQLite backup folds checkpoint WAL pages into the copy. Never
    # ship a live db plus separately copied WAL/SHM and hope they are coherent.
    dest.mkdir(parents=True)
    for directory,dirs,files in os.walk(source,followlinks=False):
        rel=Path(directory).relative_to(source);out=dest/rel;out.mkdir(exist_ok=True)
        for name in dirs+files:
            if (Path(directory)/name).is_symlink():raise TenantAccessDenied('legacy symlink detected during copy')
        for name in files:
            if name.endswith(('-wal','-shm')):continue
            src=Path(directory)/name;target=out/name
            if name.endswith(('.sqlite','.sqlite3','.db')):_sqlite_backup(src,target)
            else:shutil.copy2(src,target)


def apply_legacy_import(database,artifacts,destination,backup_dir,mapping,*,reviewed_digest,reviewed_source_digest,connection,failpoint=None):
    """Caller supplies a dedicated operator connection, never a user-serving one.

    Call only while legacy sources are quiescent. A concurrent source writer can
    invalidate the reviewed artifact snapshot and is an operator rollout blocker.
    No automatic ownership inference or overwrite. One import per empty target.
    """
    database,artifacts,destination,backup_dir=map(Path,(database,artifacts,destination,backup_dir))
    from psycopg.pq import TransactionStatus
    if connection.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('import requires an idle dedicated operator connection')
    connection.execute("SET client_encoding='UTF8'");connection.commit()
    plan=plan_legacy_import(database,artifacts,mapping)
    if reviewed_digest!=plan['mapping_digest']:raise ValueError('reviewed mapping changed')
    if reviewed_source_digest!=plan['source_digest']:raise ValueError('reviewed source changed')
    if destination.exists() and destination.is_symlink():raise TenantAccessDenied('destination symlink')
    backup_dir.mkdir(parents=True,exist_ok=False)
    backup=backup_dir/'control-plane.sqlite';_sqlite_backup(database,backup)
    # Resolve the mapping again against the coherent control-plane backup.
    plan=plan_legacy_import(backup,artifacts,mapping)
    if plan['source_digest']!=reviewed_source_digest:raise ValueError('source changed while taking backup')
    operation=uuid.uuid4().hex
    staging=destination/'.imports'/operation;staging.mkdir(parents=True,exist_ok=False)
    receipt=backup_dir/'receipt.json'
    record={'operation':operation,'mapping_digest':reviewed_digest,'state':'prepared',
            'source_digest':reviewed_source_digest,'promotion_intents':[],'published':[],'source_backup':str(backup),'quarantine':plan['quarantine']}
    _write_receipt(receipt,record)
    promoted=[]
    try:
        for sid,owner in plan['mapping']['sessions'].items():
            target=session_root(destination,TenantScope(owner),sid)
            if target.exists():raise ValueError('destination session already exists')
            _copy_session(artifacts/sid,staging/sid)
        with sqlite3.connect(backup) as db:
            db.row_factory=sqlite3.Row
            with connection.transaction():
                connection.execute('LOCK TABLE tasks,runs IN ACCESS EXCLUSIVE MODE')
                if connection.execute('SELECT (SELECT count(*) FROM tasks)+(SELECT count(*) FROM runs)').fetchone()[0]:
                    raise ValueError('legacy import requires empty target tasks/runs')
                # A source snapshot remains recoverable even after rollback.
                for table,key,owners in [('tasks','task_id',mapping['tasks']),('runs','run_id',mapping['runs'])]:
                    for identity,owner in owners.items():
                        row=dict(db.execute(f'SELECT * FROM {table} WHERE {key}=?',(identity,)).fetchone())
                        row['owner_id']=TenantScope(owner).owner_id
                        if table=='tasks':row['source_dir']=None
                        _insert(connection,table,row)
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in CHILDREN+RUN_CHILDREN:
                    if table not in tables:continue
                    key='task_id' if table in CHILDREN else 'run_id'
                    owners=mapping['tasks'] if key=='task_id' else mapping['runs']
                    for identity,owner in owners.items():
                        for source in db.execute(f'SELECT * FROM {table} WHERE {key}=?',(identity,)):
                            row=dict(source)
                            if table=='audit_log':row['owner_id']=owner
                            if table=='usage':row['user_id']=owner
                            if table in ('task_artifacts','artifacts'):
                                # Legacy absolute refs are not trusted. Canonical
                                # session discovery supplies authorized paths.
                                row['ref']='quarantined-legacy-ref'
                            _insert(connection,table,row)
                for sid,task in plan['session_tasks'].items():
                    owner=mapping['sessions'][sid]
                    generation=db.execute('SELECT generation FROM tasks WHERE task_id=?',(task,)).fetchone()[0]
                    connection.execute('INSERT INTO engine_sessions VALUES (%s,%s,%s,%s,%s)',(sid,owner,task,generation,time.time()))
                    target=session_root(destination,TenantScope(owner),sid)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    # No overwrite; root checked again after preparation.
                    if target.exists():raise ValueError('destination appeared during import')
                    record['promotion_intents'].append(str(target));_write_receipt(receipt,record)
                    (staging/sid).rename(target);promoted.append(target)
                    record['published'].append(str(target));_write_receipt(receipt,record)
                for table in ('events','review_comments','audit_log','answer_feedback','task_artifacts','usage'):
                    connection.execute("SELECT setval(pg_get_serial_sequence(%s,'id'),COALESCE((SELECT max(id) FROM "+table+"),1),EXISTS(SELECT 1 FROM "+table+"))",(table,))
                if failpoint:failpoint('before_commit')
                if plan_legacy_import(database,artifacts,mapping)['source_digest']!=reviewed_source_digest:
                    raise ValueError('legacy source changed during import')
                record['state']='commit_pending';_write_receipt(receipt,record)
            record['state']='committed';_write_receipt(receipt,record)
    except BaseException:
        # A commit_pending interruption is not assumed rolled back. An operator
        # must compare DB rows to the receipt before deciding what to remove.
        if record['state']!='commit_pending':
            for target in reversed(promoted):shutil.rmtree(target)
            record['state']='rolled_back';_write_receipt(receipt,record)
        raise
    finally:
        if staging.exists():shutil.rmtree(staging)
    return record


def _insert(connection,table,row):
    from psycopg import sql
    # Target schema drives compatibility; unknown legacy columns aren't imported.
    columns={(r[0].decode() if isinstance(r[0],bytes) else r[0]) for r in connection.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',('public',table))}
    selected={key:value for key,value in row.items() if key in columns}
    query=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
        sql.SQL(',').join(map(sql.Identifier,selected)),sql.SQL(',').join(sql.Placeholder() for _ in selected))
    connection.execute(query,tuple(selected.values()))


def reconcile_import(receipt,connection,mapping):
    """Read-only closeout after an uncertain process/commit interruption.

    Never deletes files, retries INSERTs or assumes absent/partial rows are safe.
    Operator decides cleanup after backup/current-state review.
    """
    receipt=Path(receipt)
    record=json.loads(receipt.read_text())
    digest=hashlib.sha256(json.dumps(mapping,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if digest!=record['mapping_digest']:raise ValueError('receipt mapping mismatch')
    found=[];missing=[];conflicts=[]
    for table,key,owners in [('tasks','task_id',mapping['tasks']),('runs','run_id',mapping['runs'])]:
        for identity,owner in owners.items():
            row=connection.execute(f'SELECT owner_id::text FROM {table} WHERE {key}=%s',(identity,)).fetchone()
            if row is None:missing.append(table+':'+identity)
            elif (row[0].decode() if isinstance(row[0],bytes) else row[0])!=owner:conflicts.append(table+':'+identity)
            else:found.append(table+':'+identity)
    paths=record.get('promotion_intents',record['published'])
    absent_files=[path for path in paths if not Path(path).is_dir()]
    if conflicts or found and missing:state='mixed_state_operator_review'
    elif missing:state='database_not_committed_files_require_review'
    elif absent_files:state='database_committed_files_missing_operator_review'
    else:state='committed_verified'
    return {'state':state,'found':found,'missing':missing,'conflicts':conflicts,'absent_files':absent_files,
            'action':'read-only reconciliation; no automatic retry or deletion'}
