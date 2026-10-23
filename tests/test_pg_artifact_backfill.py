"""PR B legacy backfill: publish manifest revisions for file-only session artifacts,
measured at backfill time. Runs against the isolated migrated Postgres (pg_harness)
with local fixture files only - no production size claim."""
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from delivery_api.artifact_backfill import backfill_artifact_manifests
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope, tenant_context, session_root

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
FIXTURES = Path(__file__).parent / 'fixtures' / 'coding-package'


@pytest.fixture
def owner():
    owner_id = str(uuid.uuid4())  # per-test: the manifest and ledger holds are owner-scoped
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute('INSERT INTO auth.users(id,email,email_confirmed_at) VALUES (%s,%s,now()) ON CONFLICT DO NOTHING',
                  (owner_id, owner_id + '@example.test'))
        c.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING', (owner_id, owner_id))
    return TenantScope(owner_id, owner_id)


@pytest.fixture
def store():
    return PostgresStore(DSN.replace('user=sandbox', 'user=delivery_app'))


def _session_files(root: Path):
    for sub in ('interview', 'spec'):
        src_dir = FIXTURES / sub
        if src_dir.is_dir():
            shutil.copytree(src_dir, root / sub)


def _task_with_session(store, scope, base):
    with tenant_context(scope):
        task = store.create_task('backfill-' + uuid.uuid4().hex)
        sid = uuid.uuid4().hex
        store.set_engine_session(task['task_id'], sid)
    root = session_root(Path(base), scope, sid)
    root.mkdir(parents=True)
    _session_files(root)
    return task['task_id'], sid, root


def test_dry_run_plans_and_measures_without_writing(store, owner, tmp_path):
    task_id, sid, root = _task_with_session(store, owner, tmp_path)
    report = backfill_artifact_manifests(store, tmp_path, owner, apply=False)
    assert report['applied'] is False and report['published'] == []
    planned = [p for p in report['planned'] if p['task_id'] == task_id]
    assert len(planned) == 1
    measured = {(a['phase_key'], a['name']): (a['byte_size'], a['sha256']) for a in planned[0]['artifacts']}
    from delivery_api.phases import PHASE_DEFINITIONS
    artifact_names = {name for phase in PHASE_DEFINITIONS for name in phase.artifacts}
    expected_files = [f for sub in ('interview', 'spec') for f in (FIXTURES / sub).iterdir()
                      if f.is_file() and f.name in artifact_names]
    assert len(measured) == len(expected_files), 'only phase artifacts are backfilled'
    for f in expected_files:
        data = f.read_bytes()
        assert measured[next(k for k in measured if k[1] == f.name)] == \
            (len(data), hashlib.sha256(data).hexdigest()), f'measurement mismatch for {f.name}'
    assert report['measurement']['overall']['count'] == len(measured)
    assert report['measurement']['basis'].startswith('local fixture')
    with tenant_context(owner):
        assert store.manifest_snapshot(task_id) is None, 'a dry run must not publish'


def test_apply_publishes_ready_revision_with_measured_rows(store, owner, tmp_path):
    task_id, sid, root = _task_with_session(store, owner, tmp_path)
    report = backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    published = [p for p in report['published'] if p['task_id'] == task_id]
    assert len(published) == 1 and published[0]['revision_id']
    with tenant_context(owner):
        snapshot = store.manifest_snapshot(task_id)
        assert snapshot is not None
        for row in snapshot['rows']:
            source = (root / ('interview' if row['phase_key'] in ('intent', 'conops') else 'spec') / row['name']).read_bytes()
            assert row['byte_size'] == len(source)
            assert row['sha256'] == hashlib.sha256(source).hexdigest()
            assert row['publication_state'] == 'published'
        first = snapshot['rows'][0]
        assert store.read_artifact_bytes(task_id, first['phase_key'], first['name']) == \
            (root / ('interview' if first['phase_key'] in ('intent', 'conops') else 'spec') / first['name']).read_bytes()


def test_apply_is_idempotent_and_skips_uncovered_tasks(store, owner, tmp_path):
    task_id, sid, root = _task_with_session(store, owner, tmp_path)
    backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    second = backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    assert second['published'] == []
    assert any(s['task_id'] == task_id and s['reason'] == 'manifest revision already published'
               for s in second['skipped'])
    with tenant_context(owner):
        other = store.create_task('backfill-' + uuid.uuid4().hex)
    third = backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    assert any(s['task_id'] == other['task_id'] and s['reason'] == 'no recorded engine session'
               for s in third['skipped'])
