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
A = TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001')


@pytest.fixture
def pgstore():
    return PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))


def _published(pgstore, tmp_path):
    with tenant_context(A):
        task = pgstore.create_task('reads-' + uuid.uuid4().hex)
        body = b'{"requirements": ["x"]}'
        path = tmp_path / 'srs.json'; path.write_bytes(body)
        pgstore.publish_task_artifact(task['task_id'], 'spec', 'srs.json', str(path),
                                      mime_type='application/json', writer_instance='host#1',
                                      validation_report={'validator':'SRSDocument','validator_version':'vmodel-contracts==0.28.0','valid':True,'error':None,'sha256':hashlib.sha256(body).hexdigest()})
        return task['task_id'], body


def test_flag_off_keeps_filesystem_projection(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', False)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        records = app._task_artifact_snapshot(task_id)
    # Legacy path (no engine session here): raw DB rows, NOT the manifest projection
    # (no 'present' marker and no publish-time verdict fields added by the projection).
    assert all('present' not in r for r in records)


def test_flag_on_serves_published_manifest_rows(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    task_id, body = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
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
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
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
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
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
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
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
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    from delivery_api.tenancy import TenantScope
    OTHER = TenantScope('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002')
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


def test_manifest_coverage_readiness_check(pgstore, tmp_path, monkeypatch):
    """Enable-readiness: a staged-but-unpublished artifact appears in the legacy
    projection but not the manifest; coverage flags it legacy_only and not ready."""
    from delivery_api import app
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        report = app.artifact_manifest_coverage(task_id)
    assert report['ready'] is True and report['legacy_only'] == []
    assert report['published'] == ['spec/srs.json']


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


def test_empty_manifest_with_uncovered_legacy_fails_loud(pgstore, tmp_path, monkeypatch):
    """Enforced readiness gate: flag ON, empty manifest, legacy artifacts present ->
    explicit 500, never a quietly empty list."""
    from delivery_api import app
    from fastapi import HTTPException
    task_id, _ = _published(pgstore, tmp_path)
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        with pgstore._postgres.transaction() as connection:
            connection.execute("UPDATE task_artifacts SET publication_state='staged' WHERE task_id=%s", (task_id,))
        with pytest.raises(HTTPException) as exc:
            app._task_artifact_snapshot(task_id)
    assert exc.value.status_code == 500 and 'coverage' in exc.value.detail


def test_empty_manifest_and_empty_legacy_is_clean(pgstore, tmp_path, monkeypatch):
    from delivery_api import app
    monkeypatch.setattr(app, 'store', pgstore)
    monkeypatch.setattr(app.config, 'ARTIFACT_MANIFEST_READS', True)
    monkeypatch.setattr(app.config, 'CLOUD_TENANTS', True)
    with tenant_context(A):
        task = pgstore.create_task('empty-' + uuid.uuid4().hex)
        assert app._task_artifact_snapshot(task['task_id']) == []
