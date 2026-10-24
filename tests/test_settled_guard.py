"""Settled-work substitution guard: a phase child may write its own phase's outputs,
never artifacts of phases already completed. Pinned before child start, checked
before publication. The canonical case: a pickup host's generator resume regenerates
architecture over the founder-approved bytes (no companion check involved)."""
import hashlib, json
import pytest
from delivery_api.artifact_publication import (pin_settled_artifacts, settled_substitutions,
                                               format_settled_substitution)

SRS = b'{"contract_version":"1","functional":[]}'
ARCH = b'{"contract_version":"1","components":[]}'
REGENERATED = b'{"contract_version":"1","components":["substituted"]}'


def _task(*pairs):
    return {'task_id': 't1', 'generation': 1,
            'phases': [{'phase_key': k, 'state': s} for k, s in pairs]}


def _session(tmp_path):
    (tmp_path / 'spec').mkdir()
    (tmp_path / 'spec' / 'srs.json').write_bytes(SRS)
    (tmp_path / 'spec' / 'srs.semantic-review.json').write_bytes(b'{"revision":"r"}')
    (tmp_path / 'spec' / 'architecture.json').write_bytes(ARCH)
    (tmp_path / 'spec' / 'architecture.decisions.json').write_bytes(b'{"revision":"d","slots":[]}')
    return tmp_path


def test_regenerated_architecture_is_caught(tmp_path):
    root = _session(tmp_path)
    task = _task(('srs', 'approved'), ('architecture', 'approved'), ('items', 'running'))
    pinned, settled = pin_settled_artifacts(task, root, current_phase='items')
    assert ('architecture', 'architecture.json') in pinned
    (root / 'spec' / 'architecture.json').write_bytes(REGENERATED)  # the full7 defect
    problems = settled_substitutions(root, pinned, settled)
    assert problems == [('changed', 'architecture', 'architecture.json')], problems
    message = format_settled_substitution('items', problems)
    assert 'never regenerated' in message and 'nothing from this run was published' in message


def test_vanished_settled_artifact_is_caught(tmp_path):
    root = _session(tmp_path)
    task = _task(('srs', 'approved'), ('architecture', 'approved'), ('items', 'running'))
    pinned, settled = pin_settled_artifacts(task, root, current_phase='items')
    (root / 'spec' / 'architecture.decisions.json').unlink()
    assert settled_substitutions(root, pinned, settled) == [('vanished', 'architecture', 'architecture.decisions.json')]


def test_appeared_settled_artifact_is_caught(tmp_path):
    root = _session(tmp_path)
    (root / 'spec' / 'architecture.json').unlink()  # pickup host missing the restore
    task = _task(('srs', 'approved'), ('architecture', 'approved'), ('items', 'running'))
    pinned, settled = pin_settled_artifacts(task, root, current_phase='items')
    assert ('architecture', 'architecture.json') not in pinned
    (root / 'spec' / 'architecture.json').write_bytes(REGENERATED)  # child did settled work
    assert settled_substitutions(root, pinned, settled) == [('appeared', 'architecture', 'architecture.json')]


def test_current_phase_outputs_and_non_declared_files_are_free(tmp_path):
    root = _session(tmp_path)
    task = _task(('srs', 'approved'), ('architecture', 'approved'), ('items', 'running'))
    pinned, settled = pin_settled_artifacts(task, root, current_phase='items')
    (root / 'spec' / 'items.json').write_bytes(b'[{"spec_id":"SPEC-1"}]')       # own output
    (root / 'spec' / 'run.log').write_text('log')                              # diagnostics
    (root / 'spec' / 'checkpoints.sqlite').write_bytes(b'ckpt')                # in-flight state
    (root / 'spec' / 'architecture.md').write_text('# view')                   # rendered view
    assert settled_substitutions(root, pinned, settled) == []


def test_identical_settled_bytes_pass(tmp_path):
    root = _session(tmp_path)
    task = _task(('srs', 'approved'), ('architecture', 'awaiting_approval'), ('items', 'running'))
    pinned, settled = pin_settled_artifacts(task, root, current_phase='items')
    (root / 'spec' / 'items.json').write_bytes(b'[]')
    assert settled_substitutions(root, pinned, settled) == []
