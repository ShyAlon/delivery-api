"""PR B producer-side publication: after a successful produce, publish the session's
COMPLETE artifact set as one validated manifest revision (+ readiness marker) in a
single transaction. The validator map is shared with the read-side legacy projection -
one definition, no drift. Gated by config.ARTIFACT_PUBLISH (default OFF)."""
from __future__ import annotations

import hashlib, mimetypes, socket, os
from pathlib import Path

# name -> (validator module, model class). Shared by artifact_publication and the
# legacy read projection in app.py.
ARTIFACT_VALIDATORS = {
    "intent_model.json":("vmodel_contracts.interview","IntentModel"),"conops.json":("vmodel_contracts.conops","ConOpsDocument"),
    "needs.json":("vmodel_contracts.needs","StakeholderNeedsSet"),"srs.json":("vmodel_contracts.srs","SRSDocument"),
    "architecture.json":("vmodel_contracts.architecture","ArchitectureOverview"),"items.json":("vmodel_contracts.spec","ItemSpec[]"),
    "spec_items.json":("vmodel_contracts.spec","SpecBundle"),"tests.json":("vmodel_contracts.verification","AcceptanceTest[]"),
    "test_specifications.json":("vmodel_contracts.verification","AcceptanceTest[]"),
    "validation.json":("vmodel_contracts.verification","ValidationPlan")}


def _validate(path: Path, validator) -> dict:
    """Run the real validator against the exact file bytes and build the bound report."""
    from .postgres_store import validator_provenance
    module_path, cls = validator
    data = path.read_bytes()
    module = __import__(module_path, fromlist=[cls])
    if cls in ("AcceptanceTest[]", "ItemSpec[]"):
        from pydantic import TypeAdapter
        item_type = getattr(module, cls[:-2])
        model = item_type
        check = lambda: TypeAdapter(list[item_type]).validate_json(data)
    else:
        model = getattr(module, cls)
        check = lambda: model.model_validate_json(data)
    try:
        check(); valid, error = True, None
    except Exception as exc:
        valid, error = False, f"{type(exc).__name__}: {str(exc)[:600]}"
    return {'validator': f'{module_path}.{cls}',
            'validator_version': validator_provenance(module_path, model),
            'valid': valid, 'error': error, 'sha256': hashlib.sha256(data).hexdigest()}


def session_artifact_files(root: Path):
    """The present phase artifacts under a session root, as (phase_key, name, path).
    Shared by publication and the legacy backfill - one walk, no drift."""
    from .phases import PHASE_DEFINITIONS
    found = []
    for phase in PHASE_DEFINITIONS:
        folder = root / ("interview" if phase.key in ("intent", "conops") else "spec")
        for filename in phase.artifacts:
            path = folder / filename
            if path.is_file():
                found.append((phase.key, filename, path))
    return found


def publish_session_revision(store, task_id: str, root: Path, *, writer_instance: str = None):
    """Publish every present phase artifact with its publish-time verdict, then mark
    the revision ready - atomically. Missing files are simply not part of the set the
    marker covers; a failed validation is RECORDED as a verdict, not an exception."""
    artifacts = []
    for phase_key, filename, path in session_artifact_files(root):
        validator = ARTIFACT_VALIDATORS.get(filename)
        artifacts.append({'phase_key': phase_key, 'name': filename, 'ref': str(path),
                          'mime_type': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                          'validation_report': _validate(path, validator) if validator else None})
    writer = writer_instance or f'{socket.gethostname()}#{os.getpid()}'
    return store.publish_task_revision(task_id, artifacts, writer_instance=writer)


def publish_committed_session(store, task_id: str, root: Path, *, phase_key: str) -> bool:
    """Publish a just-committed phase's artifact set under config.ARTIFACT_PUBLISH.

    Failure is visible and recoverable: the settled work is never rerun - the error
    is recorded on the task and the same durable bytes can be republished later.
    Returns True when a revision was published, False when the flag is off."""
    from . import config
    if not (config.ARTIFACT_PUBLISH and hasattr(store, 'publish_task_revision')):
        return False
    try:
        publish_session_revision(store, task_id, root)
        return True
    except Exception as exc:
        store.add_event(task_id, "error",
            f"Artifact publication failed for the committed {phase_key} output; the settled bytes stay on this host and can be republished without rerunning the phase: {type(exc).__name__}: {str(exc)[:240]}",
            phase_key=phase_key)
        return False
