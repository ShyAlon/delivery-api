"""Commit-time publication failure recovery (PR C): the supported retry route is
the existing operator backfill - it republishes the durable committed bytes with
no model work, and skips tasks that already have a ready revision. Real store,
isolated migrated Postgres."""
import os
from pathlib import Path

import pytest

from delivery_api import artifact_publication, config
from delivery_api.artifact_backfill import backfill_artifact_manifests
from delivery_api.tenancy import tenant_context
from test_pg_artifact_backfill import owner, store, _task_with_session  # noqa: F401 - shared pg fixtures

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')


def test_failed_commit_publication_recovers_through_the_operator_backfill(store, owner, tmp_path, monkeypatch):
    task_id, sid, root = _task_with_session(store, owner, tmp_path)

    # The commit-time publication fails (fault injected at the store boundary):
    # the error is recorded with its recovery route, nothing is published.
    monkeypatch.setattr(config, 'ARTIFACT_PUBLISH', True, raising=False)
    monkeypatch.setattr(store, 'publish_task_revision',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('database down')))
    with tenant_context(owner):  # the runner always publishes inside the tenant scope
        assert artifact_publication.publish_committed_session(store, task_id, root, phase_key='needs') is False
        assert store.manifest_snapshot(task_id) is None, 'a failed publication leaves no ready revision'
        events = [e for e in store.events(task_id) if e['kind'] == 'error']
        assert events and 'ops-backfill-artifact-manifest.py' in events[-1]['message'], events

    # The supported retry route: the operator backfill republishes the SAME durable
    # bytes (no model work exists on this path by construction).
    monkeypatch.undo()
    monkeypatch.setattr(config, 'ARTIFACT_PUBLISH', True, raising=False)
    report = backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    assert any(p['task_id'] == task_id for p in report['published']), report
    with tenant_context(owner):
        snapshot = store.manifest_snapshot(task_id)
        assert snapshot is not None and snapshot['rows'], 'the recovered revision is ready'
        for row in snapshot['rows']:
            data = store.read_artifact_bytes_sha(row['sha256'])  # hash-verified read
            assert data == (root / ('interview' if row['phase_key'] in ('intent', 'conops') else 'spec') / row['name']).read_bytes()

    # The route is idempotent: a second backfill skips the recovered task.
    again = backfill_artifact_manifests(store, tmp_path, owner, apply=True)
    assert not again['published'] and any(s['task_id'] == task_id for s in again['skipped']), again
