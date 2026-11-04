from pathlib import Path
"""PR B store unit: manifest publish + hash-verified reads on the Postgres adapter.

Runs against the isolated migrated Postgres (pg_harness) as delivery_app with
tenant scopes - never a real database. Publication is one atomic
demote-and-promote per artifact key; reads are manifest-only and raise on
missing or corrupt rows - never a filesystem fallback.
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
B = TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2')


@pytest.fixture
def store(): return PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))


def publish(store, task_id, name, body: bytes, writer='host#1'):
    tmp = os.path.join(os.environ['PYTEST_TMPDIR'], name)
    with open(tmp, 'wb') as f: f.write(body)
    return store.publish_task_artifact_bytes(task_id, 'spec', name, Path(tmp).read_bytes(), mime_type='application/json',
                                       writer_instance=writer, validation_report={'validator':'SRSDocument','validator_version':'vmodel-contracts==0.28.0','valid':True,'error':None,'sha256':hashlib.sha256(body).hexdigest()})


def test_publish_records_measured_manifest_and_serves_hash_verified_bytes(store, tmp_path):
    os.environ['PYTEST_TMPDIR'] = str(tmp_path)
    body = b'{"requirements": ["one", "two"]}'
    with tenant_context(A):
        task = store.create_task('manifest-' + uuid.uuid4().hex)
        row = store.publish_task_artifact_bytes(task['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, body)).read_bytes(),
                                          mime_type='application/json', writer_instance='host#1',
                                          validation_report={'validator':'SRSDocument','validator_version':'vmodel-contracts==0.28.0','valid':True,'error':None,'sha256':hashlib.sha256(body).hexdigest()})
        assert row['publication_state'] == 'published'
        assert row['sha256'] == hashlib.sha256(body).hexdigest()
        assert row['byte_size'] == len(body)
        assert row['validator'] == 'SRSDocument' and row['valid'] is True
        assert store.read_artifact_bytes(task['task_id'], 'spec', 'srs.json') == body


def _p(tmp_path, body, name='srs.json'):
    path = tmp_path / name
    path.write_bytes(body)
    return str(path)


def test_republish_supersedes_exactly_one_pointer(store, tmp_path):
    with tenant_context(A):
        task = store.create_task('manifest-' + uuid.uuid4().hex)
        store.publish_task_artifact_bytes(task['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, b'v1', 'v1.json')).read_bytes(),
                                    mime_type='application/json', writer_instance='host#1')
        store.publish_task_artifact_bytes(task['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, b'v2-longer', 'v2.json')).read_bytes(),
                                    mime_type='application/json', writer_instance='host#2')
        manifest = store.manifest_artifacts(task['task_id'])
        published = [r for r in manifest if r['publication_state'] == 'published']
        superseded = [r for r in manifest if r['publication_state'] == 'superseded']
        assert len(published) == 1 and published[0]['writer_instance'] == 'host#2'
        assert len(superseded) == 1
        assert store.read_artifact_bytes(task['task_id'], 'spec', 'srs.json') == b'v2-longer'


def test_conflicting_bytes_for_recorded_hash_rejected(store, tmp_path):
    with tenant_context(A):
        task = store.create_task('manifest-' + uuid.uuid4().hex)
        body = b'original content'
        store.publish_task_artifact_bytes(task['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, body)).read_bytes(),
                                    mime_type='application/json', writer_instance='host#1')
        digest = hashlib.sha256(body).hexdigest()
        # Corrupt the content-addressed row out-of-band (superuser; the app role cannot UPDATE):
        import psycopg
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("UPDATE artifact_bytes SET bytes=%s WHERE owner_id=%s AND sha256=%s", (b'forged', A.owner_id, digest))
        with pytest.raises(ValueError, match='hash verification'):
            store.read_artifact_bytes(task['task_id'], 'spec', 'srs.json')


def test_tenant_isolation_on_manifest_and_bytes(store, tmp_path):
    with tenant_context(A):
        task = store.create_task('manifest-' + uuid.uuid4().hex)
        store.publish_task_artifact_bytes(task['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, b'private-a')).read_bytes(),
                                    mime_type='application/json', writer_instance='host#1')
    with tenant_context(B):
        assert store.manifest_artifacts(task['task_id']) == []
        with pytest.raises(Exception):
            store.read_artifact_bytes(task['task_id'], 'spec', 'srs.json')
        # Same content under B is an independent row: no cross-tenant dedup oracle.
        own = store.create_task('manifest-' + uuid.uuid4().hex)
        store.publish_task_artifact_bytes(own['task_id'], 'spec', 'srs.json', Path(_p(tmp_path, b'private-a', 'b.json')).read_bytes(),
                                    mime_type='application/json', writer_instance='host#1')
        assert store.read_artifact_bytes(own['task_id'], 'spec', 'srs.json') == b'private-a'


def test_missing_artifact_raises_no_fallback(store):
    with tenant_context(A):
        task = store.create_task('manifest-' + uuid.uuid4().hex)
        with pytest.raises(KeyError):
            store.read_artifact_bytes(task['task_id'], 'spec', 'never-published.json')
        assert store.manifest_artifacts(task['task_id']) == []
