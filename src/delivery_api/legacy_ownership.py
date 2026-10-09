"""Explicit reviewed mapping for legacy import. No first-access ownership claim.

Produces a plan only. Applying data/file migration remains a separate operator
step, after a backup and review. Ownerless or unlinked records stay quarantined.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import sqlite3
from .tenancy import TenantScope,TenantAccessDenied


def _source_digest(db,session_paths):
    digest=hashlib.sha256()
    # iterdump reflects logical SQLite content, including WAL-backed pages,
    # instead of hashing a potentially stale main-file-only snapshot.
    for line in db.iterdump():digest.update((line+'\n').encode())
    for sid,path in sorted(session_paths.items()):
        for item in sorted(path.rglob('*')):
            if item.is_symlink():raise TenantAccessDenied('legacy symlink')
            if item.is_file():
                digest.update((sid+'/'+str(item.relative_to(path))).encode())
                with item.open('rb') as stream:
                    while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def plan_legacy_import(database:Path, artifacts:Path, mapping:dict) -> dict:
    if not database.is_file() or database.is_symlink():
        raise ValueError('regular legacy database file required')
    if not isinstance(mapping,dict) or set(mapping) != {'tasks','runs','sessions'}:
        raise ValueError('explicit tasks/runs/sessions mapping required')
    for kind,entries in mapping.items():
        if not isinstance(entries,dict):raise ValueError(f'{kind} mapping must be an object')
        for identity,owner in entries.items():
            if not isinstance(identity,str) or not identity:raise ValueError('invalid legacy identity')
            TenantScope(owner)
    # mode=ro stops this planner from creating/updating the source DB.
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        task_rows=[dict(r) for r in db.execute('SELECT task_id,owner_id,engine_session_id FROM tasks')]
        run_rows=[dict(r) for r in db.execute('SELECT run_id,owner_id FROM runs')]
    base=artifacts.resolve()
    sessions={path.name:path for path in base.iterdir() if path.is_dir() and path.name not in ('tenants','packages')} if base.is_dir() else {}
    for kind,valid in [('tasks',{r['task_id'] for r in task_rows}),('runs',{r['run_id'] for r in run_rows}),('sessions',set(sessions))]:
        unknown=set(mapping[kind])-valid
        if unknown:raise ValueError(f'{kind} mapping contains unknown IDs: '+', '.join(sorted(unknown)))
    bindings={}
    for row in task_rows:
        task=row['task_id'];owner=mapping['tasks'].get(task);session=row['engine_session_id']
        if owner and row['owner_id'] and row['owner_id']!=owner:
            raise ValueError('explicit mapping conflicts with recorded task owner')
        if session and owner:
            if mapping['sessions'].get(session)!=owner:
                raise ValueError('mapped task requires matching explicit session ownership')
            if session in bindings:raise ValueError('session has multiple task bindings')
            bindings[session]=task
    for row in run_rows:
        owner=mapping['runs'].get(row['run_id'])
        if owner and row['owner_id'] and row['owner_id']!=owner:
            raise ValueError('explicit mapping conflicts with recorded run owner')
    for session in mapping['sessions']:
        if session not in bindings:raise ValueError('session lacks explicitly mapped task binding')
        path=sessions[session]
        if path.is_symlink() or path.resolve().parent!=base:raise TenantAccessDenied('legacy session escapes root')
        import os
        for directory,dirs,files in os.walk(path,followlinks=False):
            if any((Path(directory)/name).is_symlink() for name in dirs+files):
                raise TenantAccessDenied('legacy session contains symlink')
    normalized={kind:{identity:TenantScope(owner).owner_id for identity,owner in entries.items()} for kind,entries in mapping.items()}
    digest=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        source_digest=_source_digest(db,{sid:sessions[sid] for sid in mapping['sessions']})
    return {'mode':'plan-only','source_digest':source_digest,'mapping_digest':digest,'mapping':normalized,'session_tasks':bindings,
            'quarantine':{'tasks':sorted({r['task_id'] for r in task_rows}-set(mapping['tasks'])),
                          'runs':sorted({r['run_id'] for r in run_rows}-set(mapping['runs'])),
                          'sessions':sorted(set(sessions)-set(mapping['sessions']))},
            'checkpoint_action':'copy only with mapped tenant/session; never import SQLite checkpoints into control-plane DB'}
