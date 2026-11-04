"""PR B gated read switch: manifest-backed snapshot and byte reads.

Default behavior is unchanged while DELIVERY_ARTIFACT_MANIFEST_READS is off;
with the flag on, reads come from the manifest (published rows only, verdicts
recorded at publish time) and failures raise - never a filesystem fallback.
"""
import hashlib
import os
import uuid
import pytest
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope, tenant_context

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
A = TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')


@pytest.fixture
def pgstore():
    return PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))


def _published(pgstore, tmp_path):
    with tenant_context(A):
        task = pgstore.create_task('reads-' + uuid.uuid4().hex)
        body = b'{"requirements": ["x"]}'
        path = tmp_path / 'srs.json'; path.write_bytes(body)
        pgstore.publish_task_revision(task['task_id'], [{'phase_key': 'spec', 'name': 'srs.json', 'ref': str(path),
                                      'mime_type': 'application/json',
                                      'validation_report': {'validator': 'SRSDocument', 'validator_version': 'vmodel-contracts==0.28.0',
                                                            'valid': True, 'error': None, 'sha256': hashlib.sha256(body).hexdigest()}}],
                                      writer_instance='host#1')
        return task['task_id'], body


def test_flag_off_still_serves_pg_publication(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', False,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        records = app._task_artifact_snapshot(task_id)
    # PG is sole authority even when an obsolete rollout flag is disabled.
    assert len(records)==1 and records[0]['present'] is True
    assert records[0]['valid'] is True and records[0]['name']=='srs.json'


def test_flag_on_serves_published_manifest_rows(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    task_id, body = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    import hashlib
    with tenant_context(A):
        records = app._task_artifact_snapshot(task_id)
    assert len(records) == 1
    r = records[0]
    assert r['name'] == 'srs.json' and r['present'] is True
    assert r['byte_size'] == len(body) and r['sha256'] == hashlib.sha256(body).hexdigest()
    assert r['validator'] == 'SRSDocument' and r['valid'] is True
    assert r['title'] == 'Requirements'


def test_flag_on_download_raises_without_filesystem_fallback(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    task_id, body = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        response = app.task_artifact_download(task_id, 'spec', 'srs.json')
        assert bytes(response.body) == body
        from fastapi import HTTPException
        with pytest.raises(HTTPException, match='404'):
            app.task_artifact_download(task_id, 'spec', 'missing.json')


def test_error_mapping_store_failure_never_404(pgstore, tmp_path, monkeypatch):
    """Database failure must surface as 5xx, never a plausible missing record."""
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)

    def boom(*a, **k): raise RuntimeError('connection lost')
    monkeypatch.setattr(pgstore, 'read_artifact_bytes', boom)
    with tenant_context(A):
        with pytest.raises(RuntimeError):
            app.task_artifact_download(task_id, 'spec', 'srs.json')


def test_error_mapping_corrupt_payload_is_500_not_404(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    monkeypatch.setattr(pgstore, 'read_artifact_bytes',
                        lambda *a, **k: (_ for _ in ()).throw(ValueError('stored bytes failed hash verification')))
    with tenant_context(A):
        with pytest.raises(HTTPException) as exc:
            app.task_artifact_download(task_id, 'spec', 'srs.json')
    assert exc.value.status_code == 500 and 'integrity' in exc.value.detail


def test_error_mapping_foreign_is_404(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    from delivery_api.tenancy import TenantScope
    OTHER = TenantScope('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-0000000000a2')
    with tenant_context(OTHER):
        with pytest.raises(HTTPException) as exc:
            app.task_artifact_download(task_id, 'spec', 'srs.json')
    assert exc.value.status_code == 404


def test_zip_reads_one_published_revision(pgstore, tmp_path):
    """Snapshot-then-republish: bytes read by the snapshot row's hash still serve the
    originally published revision, never a mixed-generation zip."""
    import hashlib as hl
    task_id, body = _published(pgstore, tmp_path)
    with tenant_context(A):
        snapshot = pgstore.manifest_artifacts(task_id)
        assert len(snapshot) == 1 and snapshot[0]['publication_state'] == 'published'
        newer = b'{"requirements": ["replaced"]}'
        path = tmp_path / 'srs.json'; path.write_bytes(newer)
        pgstore.publish_task_artifact(task_id, 'spec', 'srs.json', str(path),
                                      writer_instance='host#1',
                                      validation_report={'validator': 'SRSDocument', 'validator_version': 'vmodel-contracts==0.28.0', 'valid': True, 'error': None,
                                                         'sha256': hl.sha256(newer).hexdigest()})
        assert pgstore.read_artifact_bytes(task_id, 'spec', 'srs.json') == newer
        assert pgstore.read_artifact_bytes_sha(snapshot[0]['sha256']) == body  # snapshot revision intact


def test_validation_report_must_bind_published_bytes(pgstore, tmp_path):
    import hashlib as hl
    body = b'{"requirements": ["x"]}'
    path = tmp_path / 'srs.json'; path.write_bytes(body)
    with tenant_context(A):
        task = pgstore.create_task('binding-' + uuid.uuid4().hex)
        with pytest.raises(ValueError, match='hash does not match'):
            pgstore.publish_task_artifact(task['task_id'], 'spec', 'srs.json', str(path),
                                          writer_instance='host#1',
                                          validation_report={'validator': 'SRSDocument', 'validator_version': 'vmodel-contracts==0.28.0', 'valid': True, 'error': None,
                                                             'sha256': hl.sha256(b'other bytes').hexdigest()})




def test_validation_report_requires_version_provenance(pgstore, tmp_path):
    body = b'{"requirements": ["x"]}'
    path = tmp_path / 'srs.json'; path.write_bytes(body)
    import hashlib as hl
    with tenant_context(A):
        task = pgstore.create_task('prov-' + uuid.uuid4().hex)
        with pytest.raises(ValueError, match='validator_version'):
            pgstore.publish_task_artifact(task['task_id'], 'spec', 'srs.json', str(path),
                                          writer_instance='host#1',
                                          validation_report={'validator': 'SRSDocument', 'valid': True, 'error': None,
                                                             'sha256': hl.sha256(body).hexdigest()})


def test_validator_provenance_records_installed_version_and_verdict_keeps_it(pgstore, tmp_path):
    from delivery_api.postgres_store import validator_provenance
    assert validator_provenance('vmodel_contracts.srs').startswith('vmodel-contracts==')
    assert validator_provenance('no_such_module.anything') == 'unknown'
    task_id, _ = _published(pgstore, tmp_path)
    with tenant_context(A):
        row = pgstore.manifest_artifacts(task_id)[0]
    # A verdict recorded under one validator version keeps that version verbatim -
    # it is never reinterpreted as the currently installed one.
    assert row['validator_version'] == 'vmodel-contracts==0.28.0'


def test_provenance_uses_record_when_top_level_metadata_is_absent(monkeypatch):
    import importlib.metadata as metadata
    from delivery_api.postgres_store import validator_provenance
    expected='vmodel-contracts=='+metadata.version('vmodel-contracts')
    monkeypatch.setattr(metadata,'packages_distributions',lambda:{})
    assert validator_provenance('vmodel_contracts.srs')==expected
    assert validator_provenance('no_such_module.anything')=='unknown'


def test_empty_manifest_with_uncovered_legacy_fails_loud(pgstore, tmp_path, monkeypatch):
    """Enforced readiness gate: flag ON, empty manifest, legacy artifacts present ->
    explicit 500, never a quietly empty list."""
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        # Migration-pending state: marker moved to another generation, not deleted
        # (the app role holds no DELETE grant on the marker table by design).
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE artifact_manifest_ready SET generation=generation+1000 WHERE task_id=%s", (task_id,))
            connection.execute("UPDATE task_artifacts SET publication_state='staged' WHERE task_id=%s", (task_id,))
        with pytest.raises(HTTPException) as exc:
            app._task_artifact_snapshot(task_id)
    assert exc.value.status_code == 500 and 'coverage' in exc.value.detail


def test_empty_manifest_and_empty_legacy_is_clean(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        task = pgstore.create_task('empty-' + uuid.uuid4().hex)
        assert app._task_artifact_snapshot(task['task_id']) == []


def test_partial_manifest_without_marker_fails_loud(pgstore, tmp_path, monkeypatch):
    """Non-empty manifest is NOT trusted without the readiness marker: one published
    artifact plus an uncovered legacy row must still 500, not serve the partial set."""
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE artifact_manifest_ready SET generation=generation+1000 WHERE task_id=%s", (task_id,))
            connection.execute("UPDATE task_artifacts SET publication_state='staged' WHERE task_id=%s AND name='srs.json'", (task_id,))
            connection.execute(
                "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at,publication_state) "
                "VALUES (%s,1,'spec','srs.json','x','application/json',1,'published')", (task_id,))
        with pytest.raises(HTTPException) as exc:
            app._task_artifact_snapshot(task_id)
    assert exc.value.status_code == 500


def test_ready_revision_served_db_only_despite_extra_legacy(pgstore, tmp_path, monkeypatch):
    """With the ready marker, the read path is pure manifest - an extra legacy row does
    not trip the gate and no filesystem comparison happens."""
    from delivery_api import app
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        with pgstore._postgres.transaction() as connection:
            connection.execute(
                "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at,publication_state) "
                "VALUES (%s,1,'spec','extra.json','x','application/json',1,'staged')", (task_id,))
        records = app._task_artifact_snapshot(task_id)
    assert [r['name'] for r in records] == ['srs.json']


def test_marker_for_other_generation_not_ready(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE tasks SET generation=99 WHERE task_id=%s", (task_id,))
        assert pgstore.manifest_ready(task_id) is False
        with pytest.raises(HTTPException) as exc:
            app._task_artifact_snapshot(task_id)
    assert exc.value.status_code == 500


def test_revision_publish_is_atomic(pgstore, tmp_path):
    """A failed member publish rolls back the whole revision: no published rows, no marker."""
    import hashlib as hl
    good = tmp_path / 'a.json'; good.write_bytes(b'{"a": 1}')
    bad = tmp_path / 'b.json'; bad.write_bytes(b'{"b": 2}')
    report = lambda p: {'validator_version': 'v==1;schema=abc', 'validator': 'V', 'valid': True,
                        'error': None, 'sha256': hl.sha256(p.read_bytes()).hexdigest()}
    with tenant_context(A):
        task = pgstore.create_task('atomic-' + uuid.uuid4().hex)
        with pytest.raises(ValueError):
            pgstore.publish_task_revision(task['task_id'], [
                {'phase_key': 'spec', 'name': 'a.json', 'ref': str(good), 'validation_report': report(good)},
                {'phase_key': 'spec', 'name': 'b.json', 'ref': str(bad),
                 'validation_report': {**report(bad), 'sha256': hl.sha256(b'wrong').hexdigest()}}],
                writer_instance='host#1')
        assert pgstore.manifest_artifacts(task['task_id']) == []
        assert pgstore.manifest_ready(task['task_id']) is False


def _revision(pgstore, task_id, tmp_path, members, writer='host#1'):
    """members: {name: bytes}; publishes the complete set with a bound report."""
    import hashlib as hl
    artifacts = []
    for name, data in members.items():
        p = tmp_path / name; p.write_bytes(data)
        artifacts.append({'phase_key': 'spec', 'name': name, 'ref': str(p),
                          'validation_report': {'validator': 'V', 'validator_version': 'v==1',
                                                'valid': True, 'error': None, 'sha256': hl.sha256(data).hexdigest()}})
    return pgstore.publish_task_revision(task_id, artifacts, writer_instance=writer)


def test_republish_supersedes_omitted_member(pgstore, tmp_path):
    with tenant_context(A):
        task = pgstore.create_task('omit-' + uuid.uuid4().hex)
        _revision(pgstore, task['task_id'], tmp_path, {'a.json': b'1', 'b.json': b'2'})
        assert {r['name'] for r in pgstore.manifest_artifacts(task['task_id']) if r['publication_state'] == 'published'} == {'a.json', 'b.json'}
        _revision(pgstore, task['task_id'], tmp_path, {'a.json': b'1'})
        states = {r['name']: r['publication_state'] for r in pgstore.manifest_artifacts(task['task_id'])}
        assert states['a.json'] == 'published' and states['b.json'] == 'superseded'
        assert pgstore.manifest_ready(task['task_id']) is True  # marker re-bound to the smaller revision


def test_empty_complete_set_is_ready(pgstore, tmp_path, monkeypatch):
    """A revision of zero artifacts is a legitimate complete set: marked ready, read
    path serves [] with no legacy comparison."""
    from delivery_api import app
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        task = pgstore.create_task('emptyrev-' + uuid.uuid4().hex)
        pgstore.publish_task_revision(task['task_id'], [], writer_instance='host#1')
        assert pgstore.manifest_ready(task['task_id']) is True
        assert app._task_artifact_snapshot(task['task_id']) == []


def test_readiness_cas_rejects_stale_expected_revision(pgstore, tmp_path):
    """The concurrent-producer mechanism: marking with an expected revision the marker
    no longer carries matches zero rows and raises (the loser's whole publish rolls back
    in real interleaving)."""
    with tenant_context(A):
        task = pgstore.create_task('cas-' + uuid.uuid4().hex)
        _revision(pgstore, task['task_id'], tmp_path, {'a.json': b'1'})
        with pgstore._postgres.transaction() as connection:
            token = pgstore._connection.set(connection)
            try:
                with pytest.raises(ValueError, match='readiness CAS failed'):
                    pgstore._mark_revision_ready(pgstore.task(task['task_id']), 'new-rev', 'hash', 'no-longer-current', 'host#2', 0.0)
            finally:
                pgstore._connection.reset(token)


def test_corrupt_marker_hash_fails_read_loud(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True,raising=False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE artifact_manifest_ready SET revision_hash='tampered' WHERE task_id=%s", (task_id,))
        with pytest.raises(HTTPException) as exc:
            app._task_artifact_snapshot(task_id)
    assert exc.value.status_code == 500 and 'integrity' in exc.value.detail


def test_provenance_labels_schema_and_source_exactly(pgstore):
    from delivery_api.postgres_store import validator_provenance
    from vmodel_contracts.srs import SRSDocument
    prov = validator_provenance('vmodel_contracts.srs', SRSDocument)
    assert prov.startswith('vmodel-contracts==') and ';schema=' in prov and ';source=' in prov
    assert not prov.endswith('source=unknown')  # editable checkout ships readable sources


def test_publish_pg_documents_runs_real_validators_with_provenance(pgstore,tmp_path):
    from delivery_api.artifact_publication import publish_document_revision
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        task=pgstore.create_task('pubrev-'+uuid.uuid4().hex);tid=task['task_id']
        pgstore.set_engine_session(tid,'pub-'+uuid.uuid4().hex)
        job=pgstore.enqueue_worker(tid,'phase:srs');cap=pgstore.claim_worker(job)
        docs=GeneratorDocuments(pgstore,cap,job)
        docs.write('spec/srs.json',b'{"not":"a valid srs"}')
        docs.write('interview/intent_model.json',b'{"not":"valid either"}')
        published=publish_document_revision(pgstore,tid,docs,writer_instance='test-only')
        rows={r['name']:r for r in pgstore.manifest_artifacts(tid)}
        assert pgstore.manifest_ready(tid) is True
        assert rows['srs.json']['validator']=='vmodel_contracts.srs.SRSDocument'
        assert rows['srs.json']['valid'] is False and rows['srs.json']['validation_error']
        assert ';schema=' in rows['srs.json']['validator_version'] and ';source=' in rows['srs.json']['validator_version']
        assert len(published)==2
        pgstore.finish_worker(job)


def test_concurrent_producers_one_pure_revision(pgstore, tmp_path):
    """Two independent connections, barrier-synchronized, publishing different complete
    revisions of the same task+generation. The readiness CAS admits exactly one first
    publisher; the other either raises (rolled back) or legitimately supersedes AFTER
    the winner commits. The final published set must be one producer's pure revision -
    never a mix - and the marker hash must match it."""
    import threading
    from delivery_api.postgres_store import PostgresStore
    other = PostgresStore(DSN.replace('user=sandbox', 'user=delivery_app'))
    with tenant_context(A):
        task = pgstore.create_task('race-' + uuid.uuid4().hex)
    tid = task['task_id']
    barrier = threading.Barrier(2)
    outcome = {}

    def publish(store, label, members):
        with tenant_context(A):
            barrier.wait(10)
            try:
                _revision(store, tid, tmp_path / label, members, writer=f'writer-{label}')
                outcome[label] = 'published'
            except ValueError as e:
                outcome[label] = f'lost: {e}'

    (tmp_path / 'wA').mkdir(); (tmp_path / 'wB').mkdir()
    threads = [threading.Thread(target=publish, args=(pgstore, 'wA', {'a.json': b'A'})),
               threading.Thread(target=publish, args=(other, 'wB', {'b.json': b'B'}))]
    for t in threads: t.start()
    for t in threads: t.join(30)
    assert not [t for t in threads if t.is_alive()], 'concurrent publish deadlocked'
    with tenant_context(A):
        published = [r for r in pgstore.manifest_artifacts(tid) if r['publication_state'] == 'published']
        names = {r['name'] for r in published}
        assert names in ({'a.json'}, {'b.json'}), f'mixed revision published: {names} (outcomes {outcome})'
        writers = {r['writer_instance'] for r in published}
        assert len(writers) == 1
        snapshot = pgstore.manifest_snapshot(tid)  # raises if marker disagrees with members
        assert snapshot is not None and snapshot['rows']
    # Either one lost the CAS outright, or the second legitimately superseded after the
    # winner committed - both orders are valid; the purity assertions above are the point.
    assert set(outcome) == {'wA', 'wB'}


def test_combined_task_snapshot_verifies_manifest_hash(pgstore,tmp_path,monkeypatch):
    from delivery_api import config
    task_id,_=_published(pgstore,tmp_path)
    monkeypatch.setattr(config,'ARTIFACT_MANIFEST_READS',True,raising=False)
    with tenant_context(A):
        snapshot=pgstore.task_snapshot(task_id)
        assert snapshot['manifest']['rows'] and snapshot['task']['task_id']==task_id
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE artifact_manifest_ready SET revision_hash='tampered' WHERE task_id=%s",(task_id,))
        with pytest.raises(ValueError,match='readiness marker'):
            pgstore.task_snapshot(task_id)


def test_snapshot_flag_off_still_checks_pg_publication_integrity(pgstore,monkeypatch,tmp_path):
    from delivery_api import config
    monkeypatch.setattr(config,'ARTIFACT_MANIFEST_READS',False,raising=False)
    task_id,_=_published(pgstore,tmp_path)
    assert 'artifact_manifest_ready' in pgstore._snapshot_artifacts_sql()
    with tenant_context(A):
        assert pgstore.task_snapshot(task_id)['manifest']['rows']
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE artifact_manifest_ready SET revision_hash='tampered' WHERE task_id=%s",(task_id,))
        with pytest.raises(ValueError,match='readiness marker'):
            pgstore.task_snapshot(task_id)
