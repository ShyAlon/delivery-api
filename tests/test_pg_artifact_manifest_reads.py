"""PR B gated read switch: manifest-backed snapshot and byte reads.

Default behavior is unchanged while DELIVERY_ARTIFACT_MANIFEST_READS is off;
with the flag on, reads come from the manifest (published rows only, verdicts
recorded at publish time) and failures raise - never a filesystem fallback.
"""
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
                                      validator='SRSDocument', valid=True)
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
