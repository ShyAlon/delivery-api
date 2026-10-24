"""PR C restore-on-read: materialize a task's COMPLETED-phase artifacts from the
published, tenant-owned, content-addressed artifact store onto this host's session
root, so a second machine can pick up the next full phase. Gated by
config.ARTIFACT_RESTORE (default OFF).

Guarantees, all enforced before a single byte is written:
- completed phases only: phases still running are never restored - in-flight staged
  bytes never cross hosts, and settled work is never regenerated;
- one consistent manifest read (readiness marker hash-verified against its members);
- every payload re-hashed against its recorded content hash (read_artifact_bytes_sha
  verifies again on read);
- mid-restore change detection: the ready revision is re-read after the byte fetches
  and must not have moved, otherwise nothing is written;
- conflicting local bytes refuse the whole batch (identical bytes are a no-op);
- writes are per-file atomic (same-directory temporary + rename).
"""
from __future__ import annotations

import os
from pathlib import Path

# Phase states whose artifacts are committed and may cross hosts. 'awaiting_approval'
# means the phase completed and its gate is waiting; 'approved'/'completed' are later.
_COMPLETED_STATES = ("completed", "approved", "awaiting_approval")
_INTERVIEW_PHASES = ("intent", "conops")


class RestoreRefused(Exception):
    """The restore could not prove integrity or ownership; nothing was written."""


def restore_completed_phases(store, task_id: str, root: Path) -> list[str]:
    """Restore this generation's published artifacts for completed phases into the
    session root. Returns the filenames written (missing only; identical are skipped).
    No ready manifest means nothing was ever published for this generation: no-op."""
    task = store.task(task_id)
    if not task:
        return []
    completed = {p["phase_key"] for p in task["phases"] if p["state"] in _COMPLETED_STATES}
    if not completed:
        return []
    snapshot = store.manifest_snapshot(task_id)  # hash-verified consistent read, or None
    if snapshot is None:
        return []
    rows = [r for r in snapshot["rows"] if r["phase_key"] in completed]
    if not rows:
        return []
    staged = []
    for row in rows:
        try:
            data = store.read_artifact_bytes_sha(row["sha256"])  # re-hashed on read
        except (KeyError, ValueError) as exc:
            raise RestoreRefused(f"published bytes for {row['name']} failed verification: {exc}") from exc
        staged.append((row, data))
    # Mid-restore change detection: the revision must not have moved while reading.
    if store.manifest_revision(task_id) != (snapshot["revision_id"], snapshot["revision_hash"]):
        raise RestoreRefused("the published revision changed during restore; nothing was written")
    writes, restored = [], []
    for row, data in staged:
        folder = root / ("interview" if row["phase_key"] in _INTERVIEW_PHASES else "spec")
        target = folder / row["name"]
        if target.exists():
            if target.read_bytes() == data:
                continue  # identical local bytes: no-op
            raise RestoreRefused(f"conflicting local bytes at {target}; restore never overwrites")
        writes.append((target, data))
        restored.append(row["name"])
    for target, data in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".restore-tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    _write_lineage(task, snapshot, rows, root)
    return restored


def _write_lineage(task, snapshot, rows, root: Path) -> None:
    """Bind every restored (or verified-identical) artifact to its published provenance:
    task/session/generation + content hash + manifest revision. Entries cover ONLY files
    this batch wrote or byte-verified; a refused batch never reaches here, so the record
    never blesses unverified local bytes. spec/lineage.json is local authority for the
    generator's resume hydration; it is not a declared artifact and never crosses hosts."""
    from spec_generator import lineage
    spec = root / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    # The record speaks GENERATOR stage names (needs/srs/architecture/items/tests/
    # validation) so stage commits and hydration agree with restore bindings.
    stage_key = {"specs": "items"}.get
    for row in rows:
        lineage.bind(spec, task_id=task["task_id"], session_id=task.get("session_id") or "",
                     generation=task["generation"], phase=stage_key(row["phase_key"], row["phase_key"]),
                     name=row["name"], sha256=row["sha256"], source="restore", revision=snapshot["revision_id"])
    inputs = {}
    for name in ("intent_model.json", "conops.json"):
        candidate = root / "interview" / name
        if candidate.is_file():
            inputs[name] = lineage.sha256_file(candidate)
    baseline = spec / "system-baseline.json"
    if baseline.is_file():
        inputs["system-baseline.json"] = lineage.sha256_file(baseline)
    if inputs:
        lineage.record_inputs(spec, task_id=task["task_id"], session_id=task.get("session_id") or "",
                              generation=task["generation"], input_hashes=inputs)
