"""The full service must verify its artifacts root at startup.

Incident 2026-10-26: a preview container served for hours with a dead /data
mount; the first engine start died inside the stop fence with
FileNotFoundError('/data/artifacts') and the user saw "Session unavailable in
this environment". A startup preflight fails the container at launch with the
real reason instead of the first interview paying for it."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('full_entry', ROOT / 'scripts' / 'docker' / 'full_entry.py')
full_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(full_entry)


def test_a_healthy_artifacts_root_passes_and_is_created(tmp_path):
    root = tmp_path / 'data' / 'artifacts'
    assert full_entry.artifacts_root_problem({'VMODEL_ARTIFACTS_DIR': str(root)}) is None
    assert root.is_dir()


def test_an_uncreatable_artifacts_root_is_named_with_the_error(tmp_path):
    blocker = tmp_path / 'a-file'
    blocker.write_text('x')
    env = {'VMODEL_ARTIFACTS_DIR': str(blocker / 'artifacts')}
    problem = full_entry.artifacts_root_problem(env)
    assert problem is not None and str(blocker / 'artifacts') in problem and 'NotADirectoryError' in problem


def test_an_unwritable_artifacts_root_is_named(tmp_path):
    root = tmp_path / 'artifacts'
    root.mkdir()
    root.chmod(0o500)
    try:
        problem = full_entry.artifacts_root_problem({'VMODEL_ARTIFACTS_DIR': str(root)})
    finally:
        root.chmod(0o700)
    if root.joinpath('.probe').exists() or problem is None and os.access(root, os.W_OK):
        pytest.skip('this user can write despite the mode (running as root)')
    assert problem is not None and 'PermissionError' in problem


def test_main_refuses_to_start_when_the_artifacts_root_is_dead(tmp_path, monkeypatch):
    blocker = tmp_path / 'a-file'
    blocker.write_text('x')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR', str(blocker / 'artifacts'))
    monkeypatch.setattr(full_entry, 'check_env', lambda env: [])
    spawned = []
    monkeypatch.setattr(full_entry.subprocess, 'Popen', lambda *a, **k: spawned.append(a) or None)
    assert full_entry.main() == 2 and spawned == []


# main() runs only in the real supervisor process. Restore its process-local
# budget and close any created pool between in-process launcher unit tests.
@pytest.fixture(autouse=True)
def supervisor_process_isolation(monkeypatch):
    from delivery_api import db_pool
    old=db_pool.PROCESS_POOL_MAX
    yield
    db_pool.close_all()
    db_pool.PROCESS_POOL_MAX=old
