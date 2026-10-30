"""Private isolated staging preparation, no promotion/install or model calls.

Trusted caller supplies immutable held lineage/options/review from owner store.
Original source writer fence must already be held; no client path grants access.
Partial failure retains private bytes for explicit review, never auto cleanup.
"""
import hashlib,re
from pathlib import Path
from copy import deepcopy
from .tenancy import session_root
from .revision_stage_evidence import validate_cloud_revision_conops
from .revision_source_review import read_source_review_snapshot,source_review_payload
from .revision_proposal import read_retained_revision_proposal


def prepare_private_revision_stage(store,capability,*,artifacts_base,lineage,lineage_sha256,options,review_snapshot):
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import check_execution
    from .revision_checkpoint_reader import read_source_checkpoint
    from spec_generator.revision_checkpoint import prepare_revision_seed
    root=session_root(Path(artifacts_base),capability.scope,capability.session_id)
    binding=cli_stop_binding()
    if (binding is None or binding[1]!=capability.generation
        or Path(binding[0].path)!=root/'interview'/'stop-request.json'):
        raise ValueError('original owned source fence required for staging')
    saved=review_snapshot['snapshot']
    expected_review=source_review_payload(lineage_sha256=lineage_sha256,checkpoint_id=lineage['source_checkpoint_id'],
        semantic_session_id=lineage['semantic_session_id'],phase=lineage['phase'],upstream_sha256=saved['upstream_sha256'])
    if review_snapshot!=expected_review:raise ValueError('exact retained review identity/digest required')
    def authority():
        capability.validate(store);check_execution()
        if read_source_review_snapshot(store,capability,artifacts_base=artifacts_base,lineage=lineage,lineage_sha256=lineage_sha256)!=review_snapshot:
            raise ValueError('source differs from retained upstream review')
    authority()
    if (lineage.get('kind')!='closed_revision_execution_lineage'
        or not isinstance(lineage.get('execution_session_id'),str)
        or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}',lineage['execution_session_id'])
        or lineage['execution_session_id'] in (capability.session_id,lineage['semantic_session_id'])):
        raise ValueError('canonical fresh private stage identity required')
    from .revision_evidence import read_revision_artifacts
    if read_revision_artifacts(store,capability,root,lineage['phase'],artifacts_base=artifacts_base)!=options['artifact_evidence']:
        raise ValueError('reviewed phase evidence changed before staging')
    source=read_source_checkpoint(store,capability,None,session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
    validate_cloud_revision_conops(source,lineage['semantic_session_id'])
    proposal=read_retained_revision_proposal(store,capability,root,lineage['proposal_id'],artifacts_base=artifacts_base,options=options)
    if proposal['document_sha256']!=lineage['proposal_document_sha256'] or proposal['proposal']['artifact_sha256']!=lineage['regenerated_artifact_sha256']:
        raise ValueError('retained proposal differs from held lineage')
    if (options['phase']!=lineage['phase'] or options['source_checkpoint_id']!=lineage['source_checkpoint_id']
        or options['preflight_digest']!=lineage['review_digest'] or options['artifact_evidence']!=lineage['approved_source_artifact_evidence']):
        raise ValueError('staging options differ from held lineage')
    expected={'interview/intent_model.json':source['intent_model'],'interview/conops.json':source['conops'],
        'spec/needs.json':source['needs'],'spec/system-baseline.json':source['system_baseline']}
    if lineage['phase']=='architecture':expected['spec/srs.json']=source['srs']
    from .pg_revision_artifacts import documents as read_documents
    upstream=read_documents(store,capability,expected)
    if {name:hashlib.sha256(raw).hexdigest() for name,raw in upstream.items()}!=review_snapshot['snapshot']['upstream_sha256']:
        raise ValueError('upstream byte inventory differs from retained review')
    prepared=prepare_revision_seed(source=source,phase=lineage['phase'],change_request=options['change_request'],session_id=lineage['semantic_session_id'])
    result=deepcopy(prepared['seed']);result['gate_feedback'].pop(lineage['phase'],None)
    result[lineage['phase']]=deepcopy(proposal['proposal']['artifact'])
    from .pg_revision_stage import retain_stage
    receipt=retain_stage(store,capability,source=source,result=result,lineage=lineage,lineage_sha256=lineage_sha256,
        change_request=options['change_request'],validate_authority=authority,upstream=upstream)
    return receipt|{'installed':False,'dispatch_started':False,'source_mutated':False}
