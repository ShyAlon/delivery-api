"""Exact private input-tree binding, including SQLite WAL sidecars."""
import hashlib,json
from pathlib import Path
from recovery_plan import digest

def tree_digest(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('regular source directory required')
    if any(p.is_symlink() for p in root.parents):raise ValueError('source ancestor symlink forbidden')
    files={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('source tree symlink forbidden')
        if p.is_file():files[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    if not files:raise ValueError('nonempty original evidence required')
    return digest(files)


def prepare_review(*,run_id,dump,source_run,runtime,maximum_expense_units):
    if type(maximum_expense_units) is not int or maximum_expense_units<=0:raise ValueError('explicit positive provider expense cap required')
    review={'version':1,'original_run_id':run_id,'reconciled_dump_sha256':hashlib.sha256(Path(dump).read_bytes()).hexdigest(),
       'original_failed_job':'edad945ddadfa63b5bd73d5546a5bf69','task_id':'c11813e4a4b7',
       'source_tree_sha256':tree_digest(source_run),'runtime_tree_sha256':tree_digest(runtime),
       'maximum_expense_units':maximum_expense_units,'new_purchase':False,'uninterrupted_acceptance':False}
    return review,digest(review)
