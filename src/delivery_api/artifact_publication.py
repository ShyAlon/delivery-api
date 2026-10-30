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
    "architecture.json":("vmodel_contracts.architecture","ArchitectureOverview"),"architecture.decisions.json":("delivery_api.stack_decisions_contract","StackDecisionsCompanion"),
    "srs.semantic-review.json":("vmodel_contracts.semantic_review","SemanticReview"),"items.json":("vmodel_contracts.spec","ItemSpec[]"),
    "spec_items.json":("vmodel_contracts.spec","SpecBundle"),"tests.json":("vmodel_contracts.verification","AcceptanceTest[]"),
    "test_specifications.json":("vmodel_contracts.verification","AcceptanceTest[]"),
    "validation.json":("vmodel_contracts.verification","ValidationPlan")}


def _validate(path: Path, validator) -> dict:
    """Run the real validator against the exact file bytes and build the bound report."""
    return validate_artifact_bytes(path.read_bytes(),validator)

def validate_artifact_bytes(data,validator):
    from .postgres_store import validator_provenance
    module_path, cls = validator
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


def pin_settled_artifacts(task, root: Path, *, current_phase: str):
    """The authoritative completed-phase snapshot, pinned BEFORE a phase child starts:
    (phase_key, name) -> sha256 for every present declared artifact of phases already
    settled ('completed'/'approved'/'awaiting_approval'), excluding the child's own
    phase. A phase child legitimately writes ONLY its own phase's artifacts (plus
    non-declared diagnostics); no settled artifact may change, vanish, or appear
    while it runs - gate-time founder mutations happen before the next child starts,
    never during one."""
    from .artifact_restore import _COMPLETED_STATES
    settled = {p['phase_key'] for p in (task or {}).get('phases', [])
               if p.get('state') in _COMPLETED_STATES} - {current_phase}
    pinned = {}
    for phase_key, filename, path in session_artifact_files(root):
        if phase_key in settled:
            pinned[(phase_key, filename)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return pinned, settled


def settled_substitutions(root: Path, pinned, settled) -> list:
    """Settled artifacts that changed, vanished, or appeared while the child ran."""
    after = {}
    for phase_key, filename, path in session_artifact_files(root):
        if phase_key in settled:
            after[(phase_key, filename)] = hashlib.sha256(path.read_bytes()).hexdigest()
    problems = []
    for key, sha in pinned.items():
        if key not in after:
            problems.append(('vanished',) + key)
        elif after[key] != sha:
            problems.append(('changed',) + key)
    for key in after:
        if key not in pinned:
            problems.append(('appeared',) + key)
    return sorted(problems)


def format_settled_substitution(phase: str, problems) -> str:
    listed = '; '.join(f'{pk}/{name} {kind}' for kind, pk, name in problems)
    return (f"Error: {phase} modified settled work from an earlier completed phase ({listed}). "
            "Settled outputs are never regenerated: the authoritative bytes stay in the published "
            "artifact store, and nothing from this run was published or approved - no committed work "
            "is lost. Likely cause: this host was missing or misread the committed upstream state. "
            f"Recovery: remove the listed substituted files from this session, reload the task so the "
            f"published bytes are restored (verified against their recorded hashes), then retry {phase} "
            "- do not rerun earlier phases.")


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
            f"Artifact publication failed for the committed {phase_key} output; the settled bytes stay on this host and no model work is rerun. Recovery: run scripts/ops-backfill-artifact-manifest.py for this owner on the producing host - it republishes these durable bytes (it skips tasks that already have a ready revision): {type(exc).__name__}: {str(exc)[:240]}",
            phase_key=phase_key)
        return False


def document_artifact_keys():
    from .phases import PHASE_DEFINITIONS
    return {(phase.key,name):('interview/' if phase.key in ('intent','conops') else 'spec/')+name
            for phase in PHASE_DEFINITIONS for name in phase.artifacts}


def publish_document_revision(store,task_id,documents,*,writer_instance=None):
    """Exact captured PG documents and validation in one fenced revision transaction.

    Declared artifacts are exclusively the document set. Non-pipeline manifest
    members (notably the launch baseline) survive unchanged, never reread from disk.
    """
    if documents.capability.task_id!=task_id:raise ValueError('document task differs')
    keys=document_artifact_keys()
    with documents.saver._cursor() as cursor:
        captured=documents._capture_cursor(cursor,list(keys.values()))
        prior=store.manifest_snapshot(task_id,documents.capability.generation)
        artifacts=[{'phase_key':r['phase_key'],'name':r['name'],'data':store.read_artifact_bytes_sha(r['sha256']),
                    'mime_type':r['mime_type'],'validation_report':({'validator':r['validator'],'validator_version':r['validator_version'],'valid':r['valid'],'error':r['validation_error'],'sha256':r['sha256']} if r['validator'] is not None else None)}
                   for r in (prior or {}).get('rows',[]) if (r['phase_key'],r['name']) not in keys]
        for (phase,name),key in keys.items():
            if key not in captured:continue
            raw=captured[key];validator=ARTIFACT_VALIDATORS.get(name)
            artifacts.append({'phase_key':phase,'name':name,'data':raw,
                'mime_type':mimetypes.guess_type(name)[0] or 'application/octet-stream',
                'validation_report':validate_artifact_bytes(raw,validator) if validator else None})
        return store.publish_task_revision_bytes(task_id,artifacts,
            writer_instance=writer_instance or f'{socket.gethostname()}#{os.getpid()}')


def publish_preserving_revision(store,task_id,replacements,*,writer_instance='control-plane'):
    """Replace exact explicit members while retaining the complete ready PG set.

    Caller must hold its authorized task operation transaction and row lock.
    """
    if store._connection.get() is None:raise ValueError('authorized artifact transaction required')
    snapshot=store.manifest_snapshot(task_id)
    artifacts=[]
    for row in (snapshot or {}).get('rows',[]):
        if (row['phase_key'],row['name']) in replacements:continue
        report=({'validator':row['validator'],'validator_version':row['validator_version'],'valid':row['valid'],
            'error':row['validation_error'],'sha256':row['sha256']} if row['validator'] is not None else None)
        artifacts.append({'phase_key':row['phase_key'],'name':row['name'],'data':store.read_artifact_bytes_sha(row['sha256']),
            'mime_type':row['mime_type'],'validation_report':report})
    for (phase,name),raw in replacements.items():
        validator=ARTIFACT_VALIDATORS.get(name)
        artifacts.append({'phase_key':phase,'name':name,'data':raw,'validation_report':validate_artifact_bytes(raw,validator) if validator else None})
    return store.publish_task_revision_bytes(task_id,artifacts,writer_instance=writer_instance)
