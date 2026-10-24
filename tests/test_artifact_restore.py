"""PR C restore-on-read: completed-phase artifacts cross hosts only as verified,
content-addressed published bytes; conflicts refuse the batch; settled work never
moves mid-restore."""
import hashlib, pytest
from pathlib import Path
from delivery_api import artifact_restore
from delivery_api.artifact_restore import restore_completed_phases, RestoreRefused

NEEDS = b'{"contract_version":"1","needs":[]}'
NEEDS_SHA = hashlib.sha256(NEEDS).hexdigest()
CONOPS = b'{"contract_version":"1","summary":"c"}'
CONOPS_SHA = hashlib.sha256(CONOPS).hexdigest()


class FakeStore:
    def __init__(self, *, phases, rows=(), revision=('rev-1', 'hash-1'), moved=False, tampered=False):
        self._phases = phases
        self._rows = list(rows)
        self._revision = revision
        self._moved = moved          # manifest_revision reports a different revision (mid-restore republish)
        self._tampered = tampered    # byte reads fail their recorded hash
    def task(self, task_id):
        return {'task_id': task_id, 'generation': 1, 'phases': self._phases}
    def manifest_snapshot(self, task_id):
        if not self._rows:
            return None
        return {'revision_id': self._revision[0], 'revision_hash': self._revision[1], 'rows': list(self._rows)}
    def manifest_revision(self, task_id):
        if self._moved:
            return ('rev-2', 'hash-2')
        return self._revision
    def read_artifact_bytes_sha(self, sha256):
        if self._tampered:
            raise ValueError('stored bytes failed hash verification')
        return {NEEDS_SHA: NEEDS, CONOPS_SHA: CONOPS}[sha256]


def _row(phase, name, sha):
    return {'phase_key': phase, 'name': name, 'sha256': sha}


def _phases(*pairs):
    return [{'phase_key': k, 'state': s} for k, s in pairs]


def test_no_ready_manifest_is_a_noop(tmp_path):
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')))
    assert restore_completed_phases(store, 't1', tmp_path) == []
    assert list(tmp_path.rglob('*')) == []


def test_missing_completed_artifacts_are_restored_with_exact_bytes(tmp_path):
    store = FakeStore(phases=_phases(('conops', 'approved'), ('needs', 'awaiting_approval')),
                      rows=[_row('conops', 'conops.json', CONOPS_SHA), _row('needs', 'needs.json', NEEDS_SHA)])
    written = restore_completed_phases(store, 't1', tmp_path)
    assert sorted(written) == ['conops.json', 'needs.json']
    assert (tmp_path / 'interview' / 'conops.json').read_bytes() == CONOPS
    assert (tmp_path / 'spec' / 'needs.json').read_bytes() == NEEDS


def test_identical_local_bytes_are_a_noop(tmp_path):
    (tmp_path / 'spec').mkdir()
    target = tmp_path / 'spec' / 'needs.json'
    target.write_bytes(NEEDS)
    before = target.stat().st_mtime_ns
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')), rows=[_row('needs', 'needs.json', NEEDS_SHA)])
    assert restore_completed_phases(store, 't1', tmp_path) == []
    assert target.stat().st_mtime_ns == before


def test_conflicting_local_bytes_refuse_the_whole_batch(tmp_path):
    (tmp_path / 'spec').mkdir()
    (tmp_path / 'spec' / 'needs.json').write_bytes(b'different local work')
    store = FakeStore(phases=_phases(('conops', 'approved'), ('needs', 'awaiting_approval')),
                      rows=[_row('conops', 'conops.json', CONOPS_SHA), _row('needs', 'needs.json', NEEDS_SHA)])
    with pytest.raises(RestoreRefused, match='conflicting local bytes'):
        restore_completed_phases(store, 't1', tmp_path)
    assert (tmp_path / 'spec' / 'needs.json').read_bytes() == b'different local work'
    assert not (tmp_path / 'interview' / 'conops.json').exists()  # no partial batch


def test_in_flight_phases_never_cross(tmp_path):
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval'), ('srs', 'running')),
                      rows=[_row('needs', 'needs.json', NEEDS_SHA), _row('srs', 'srs.json', CONOPS_SHA)])
    assert restore_completed_phases(store, 't1', tmp_path) == ['needs.json']
    assert not (tmp_path / 'spec' / 'srs.json').exists()


def test_a_revision_that_moves_mid_restore_writes_nothing(tmp_path):
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')),
                      rows=[_row('needs', 'needs.json', NEEDS_SHA)], moved=True)
    with pytest.raises(RestoreRefused, match='changed during restore'):
        restore_completed_phases(store, 't1', tmp_path)
    assert not (tmp_path / 'spec' / 'needs.json').exists()


def test_bytes_failing_their_recorded_hash_refuse(tmp_path):
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')),
                      rows=[_row('needs', 'needs.json', NEEDS_SHA)], tampered=True)
    with pytest.raises(RestoreRefused, match='failed verification'):
        restore_completed_phases(store, 't1', tmp_path)
    assert not (tmp_path / 'spec' / 'needs.json').exists()
