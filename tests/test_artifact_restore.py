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



@pytest.mark.producer_integration
def test_missing_completed_artifacts_are_restored_with_exact_bytes(tmp_path):
    pytest.importorskip('spec_generator.lineage')
    store = FakeStore(phases=_phases(('conops', 'approved'), ('needs', 'awaiting_approval')),
                      rows=[_row('conops', 'conops.json', CONOPS_SHA), _row('needs', 'needs.json', NEEDS_SHA)])
    written = restore_completed_phases(store, 't1', tmp_path)
    assert sorted(written) == ['conops.json', 'needs.json']
    assert (tmp_path / 'interview' / 'conops.json').read_bytes() == CONOPS
    assert (tmp_path / 'spec' / 'needs.json').read_bytes() == NEEDS



@pytest.mark.producer_integration
def test_identical_local_bytes_are_a_noop(tmp_path):
    pytest.importorskip('spec_generator.lineage')
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



@pytest.mark.producer_integration
def test_in_flight_phases_never_cross(tmp_path):
    pytest.importorskip('spec_generator.lineage')
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



@pytest.mark.producer_integration
def test_restore_writes_lineage_binding_only_for_verified_bytes(tmp_path):
    pytest.importorskip('spec_generator.lineage')
    from spec_generator import lineage
    store = FakeStore(phases=_phases(('conops', 'approved'), ('needs', 'awaiting_approval')),
                      rows=[_row('conops', 'conops.json', CONOPS_SHA), _row('needs', 'needs.json', NEEDS_SHA)])
    restore_completed_phases(store, 't1', tmp_path)
    record = lineage.load(tmp_path / 'spec')
    assert record is not None, 'a successful restore leaves the authority record'
    needs = lineage.lookup(record, 'needs', 'needs.json')
    assert needs['sha256'] == NEEDS_SHA and needs['source'] == 'restore' and needs['revision'] == 'rev-1'
    assert needs['task_id'] == 't1' and needs['generation'] == '1'
    # launch inputs record the scheme-tagged identity (lineage contract); the fixture's
    # canonical bytes hash identically under jsonv1, so the tag is the only change
    assert record['inputs'].get('conops.json') == 'jsonv1:' + CONOPS_SHA
    # a second identical restore re-binds without growth or error
    restore_completed_phases(store, 't1', tmp_path)
    assert len(lineage.load(tmp_path / 'spec')['entries']) == 2


def test_refused_restore_writes_no_lineage(tmp_path):
    (tmp_path / 'spec').mkdir()
    (tmp_path / 'spec' / 'needs.json').write_bytes(b'conflicting local bytes')
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')),
                      rows=[_row('needs', 'needs.json', NEEDS_SHA)])
    with pytest.raises(RestoreRefused):
        restore_completed_phases(store, 't1', tmp_path)
    assert not (tmp_path / 'spec' / 'lineage.json').exists(), 'a refused batch never blesses local bytes'



@pytest.mark.producer_integration
def test_specs_phase_binds_under_the_generator_stage_name(tmp_path):
    pytest.importorskip('spec_generator.lineage')
    from spec_generator import lineage
    store = FakeStore(phases=_phases(('specs', 'approved')),
                      rows=[_row('specs', 'items.json', NEEDS_SHA)])
    restore_completed_phases(store, 't1', tmp_path)
    record = lineage.load(tmp_path / 'spec')
    assert lineage.lookup(record, 'items', 'items.json') is not None
    assert lineage.lookup(record, 'specs', 'items.json') is None


def test_missing_spec_generator_refuses_clearly_before_any_write(tmp_path, monkeypatch):
    """Restore's batch ends in the generator-owned authority record; a host without
    spec-generator (control-plane-only image) must refuse BEFORE writing bytes."""
    import sys
    monkeypatch.setitem(sys.modules, 'spec_generator', None)
    monkeypatch.setitem(sys.modules, 'spec_generator.lineage', None)
    store = FakeStore(phases=_phases(('needs', 'awaiting_approval')),
                      rows=[_row('needs', 'needs.json', NEEDS_SHA)])
    with pytest.raises(RestoreRefused, match='spec-generator is not importable'):
        restore_completed_phases(store, 't1', tmp_path)
    assert not (tmp_path / 'spec').exists(), 'a dependency-less host writes nothing'
