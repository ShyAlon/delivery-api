"""PR B legacy backfill: publish manifest revisions for tasks whose artifacts exist
only as session files, so manifest-backed reads (ARTIFACT_MANIFEST_READS) can be
enabled without existing artifacts vanishing (see app.artifact_manifest_coverage -
the readiness gate refuses partial authority).

Every covered file is measured at backfill time (the manifest records byte_size +
sha256 from the real bytes) and the run reports the measured byte-size distribution.
A LOCAL run measures local fixtures only - it says nothing about production sizes,
and the storage bound reopens if later real numbers surprise (per the PR design).
Dry by default: without apply=True nothing is written.
"""
from __future__ import annotations

import hashlib
import statistics
from pathlib import Path

from .artifact_publication import session_artifact_files, publish_session_revision
from .tenancy import TenantScope, tenant_context, session_root

WRITER = 'ops-backfill-artifact-manifest'


def _distribution(sizes):
    if not sizes:
        return {'count': 0}
    ordered = sorted(sizes)
    return {'count': len(ordered), 'min': ordered[0], 'median': int(statistics.median(ordered)),
            'p95': ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            'max': ordered[-1], 'total_bytes': sum(ordered)}


def backfill_artifact_manifests(store, artifacts_base, scope: TenantScope, *, apply: bool = False,
                                writer_instance: str = WRITER):
    """Backfill one owner's tasks. Skips (with reasons) tasks that have no recorded
    engine-session binding, no artifact files, or an already-ready manifest revision
    (idempotent: re-running never republishes)."""
    report = {'owner_id': scope.owner_id, 'applied': apply, 'published': [], 'planned': [],
              'skipped': [], 'measurement': None}
    per_name = {}
    with tenant_context(scope):
        for task in store.list_tasks():
            task_id = task['task_id']
            session_id = store.engine_session(task_id)
            if not session_id:
                report['skipped'].append({'task_id': task_id, 'reason': 'no recorded engine session'})
                continue
            if store.manifest_snapshot(task_id) is not None:
                report['skipped'].append({'task_id': task_id, 'reason': 'manifest revision already published'})
                continue
            root = session_root(Path(artifacts_base), scope, session_id)
            files = session_artifact_files(root)
            if not files:
                report['skipped'].append({'task_id': task_id, 'reason': 'no artifact files under the session root'})
                continue
            measured = []
            for phase_key, name, path in files:
                data = path.read_bytes()
                measured.append({'phase_key': phase_key, 'name': name, 'byte_size': len(data),
                                 'sha256': hashlib.sha256(data).hexdigest()})
                per_name.setdefault(name, []).append(len(data))
            if not apply:
                report['planned'].append({'task_id': task_id, 'artifacts': measured})
                continue
            publish_session_revision(store, task_id, root, writer_instance=writer_instance)
            snapshot = store.manifest_snapshot(task_id)
            if snapshot is None:
                raise RuntimeError(f'backfill published no ready revision for task {task_id}')
            rows = [{'phase_key': r['phase_key'], 'name': r['name'], 'byte_size': r['byte_size'],
                     'sha256': r['sha256']} for r in snapshot['rows']]
            if {(r['phase_key'], r['name'], r['byte_size'], r['sha256']) for r in rows} != \
               {(m['phase_key'], m['name'], m['byte_size'], m['sha256']) for m in measured}:
                raise RuntimeError(f'backfill measurement disagrees with the published manifest for task {task_id}')
            report['published'].append({'task_id': task_id, 'revision_id': snapshot['revision_id'],
                                        'artifacts': rows})
    all_sizes = [size for sizes in per_name.values() for size in sizes]
    report['measurement'] = {'per_artifact': {name: _distribution(sizes) for name, sizes in sorted(per_name.items())},
                             'overall': _distribution(all_sizes),
                             'basis': 'local fixture files only - no production size claim'}
    return report
