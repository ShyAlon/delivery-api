"""Pinned private staging manifest readback, not promotion or installation.

Caller owns same-host source/destination writer exclusion. Manifest digest and
lineage must come from the held control-plane plan, not client-supplied paths.
"""
from pathlib import Path
from .tenancy import session_root




def read_validated_staged_receipt(store,capability,*,artifacts_base,lineage,lineage_sha256,manifest_sha256,options,promoted=False):
    """Compose exact PG stage receipt and typed semantic readback.

    Caller derives lineage/options from immutable held records and owns writer
    exclusion. This validates staging only, not owner approval or installation.
    """
    from .closed_revision_job import _options
    options=_options(options)
    if (options['phase']!=lineage.get('phase') or options['source_checkpoint_id']!=lineage.get('source_checkpoint_id')
        or options['preflight_digest']!=lineage.get('review_digest')
        or options['artifact_evidence']!=lineage.get('approved_source_artifact_evidence')):
        raise ValueError('staged options differ from held lineage')
    from .pg_revision_stage import read_stage
    row=store.decision_review(capability.task_id,'pg-revision-stage:'+lineage_sha256,'prepared')
    if row is None:raise ValueError('owned immutable PG stage missing')
    receipt={'manifest_sha256':manifest_sha256,'checkpoint_id':row['checkpoint_id']}
    observed=read_stage(store,capability,receipt,lineage=lineage,lineage_sha256=lineage_sha256)
    if row['change_request']!=options['change_request']:raise ValueError('stage change request differs from reviewed options')
    root=session_root(Path(artifacts_base),capability.scope,capability.session_id)
    from .revision_evidence import read_revision_artifacts
    if read_revision_artifacts(store,capability,root,lineage['phase'],artifacts_base=artifacts_base)!=options['artifact_evidence']:
        raise ValueError('reviewed source artifacts changed before staged readiness')
    from .revision_checkpoint_reader import read_source_checkpoint
    source=read_source_checkpoint(store,capability,None,session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
    expected={'interview/intent_model.json':source['intent_model'],'interview/conops.json':source['conops'],
        'spec/needs.json':source['needs'],'spec/system-baseline.json':source['system_baseline']}
    if lineage['phase']=='architecture':expected['spec/srs.json']=source['srs']
    from .pg_revision_artifacts import documents as read_documents
    if read_documents(store,capability,expected)!=observed['upstream']:
        raise ValueError('staged upstream bytes differ from approved source files')
    return receipt


def validate_cloud_revision_conops(source,semantic_session_id):
    """Cloud staging preserves existing typed ConOps requirement, never heals."""
    from vmodel_contracts import ConOpsDocument,check_compatible
    conops=source.get('conops') if isinstance(source,dict) else None
    if not isinstance(conops,dict):raise ValueError('cloud revision staging requires typed source ConOps')
    check_compatible(conops)
    typed=ConOpsDocument.model_validate(conops)
    if typed.session_id!=semantic_session_id:raise ValueError('cloud ConOps semantic source identity mismatch')
    return typed
