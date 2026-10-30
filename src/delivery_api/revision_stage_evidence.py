"""Pinned private staging manifest readback, not promotion or installation.

Caller owns same-host source/destination writer exclusion. Manifest digest and
lineage must come from the held control-plane plan, not client-supplied paths.
"""
import hashlib,json,os,re,stat
from pathlib import Path
from .producer_inputs import _open_directory,_identity,_content_stamp,MAX_DOCUMENT_BYTES
from .tenancy import session_root,TenantAccessDenied




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


def read_approved_upstream_bytes(root,*,expected_documents):
    return {name:hashlib.sha256(raw).hexdigest() for name,raw in read_approved_upstream_documents(root,expected_documents=expected_documents).items()}


def read_approved_upstream_documents(root,*,expected_documents):
    """Read exact original bytes matching approved checkpoint values, no healing.

    Caller supplies authorized canonical root and typed source checkpoint values.
    Missing optional-looking documents fail closed; absence needs an explicit
    reviewed inventory policy, never a fabricated null file.
    """
    allowed={'interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json','spec/srs.json'}
    if not isinstance(expected_documents,dict) or not expected_documents or not set(expected_documents)<=allowed:
        raise ValueError('canonical approved upstream inventory required')
    parent=_open_directory(root);records=[];documents={}
    try:
        for name,value in expected_documents.items():
            directory,filename=name.split('/');dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            try:
                fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
                try:
                    before=os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_DOCUMENT_BYTES:
                        raise ValueError('bounded regular approved upstream file required')
                    with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(MAX_DOCUMENT_BYTES+1)
                    if len(raw)>MAX_DOCUMENT_BYTES or _content_stamp(os.fstat(fd))!=_content_stamp(before):
                        raise TenantAccessDenied('approved upstream bytes changed during read')
                    if json.loads(raw)!=value:raise ValueError('approved source file differs from checkpoint channel')
                    if _content_stamp(os.stat(filename,dir_fd=dfd,follow_symlinks=False))!=_content_stamp(before):
                        raise TenantAccessDenied('approved upstream namespace changed')
                    records.append((directory,filename,_identity(dfd),_content_stamp(before)))
                    documents[name]=raw
                finally:os.close(fd)
            finally:os.close(dfd)
        current=_open_directory(root)
        try:
            if _identity(current)!=_identity(parent):raise TenantAccessDenied('approved upstream root changed')
            for directory,filename,identity,stamp in records:
                dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
                try:
                    if _identity(dfd)!=identity or _content_stamp(os.stat(filename,dir_fd=dfd,follow_symlinks=False))!=stamp:
                        raise TenantAccessDenied('approved upstream inventory changed across readback')
                finally:os.close(dfd)
        finally:os.close(current)
        return documents
    finally:os.close(parent)


def validate_cloud_revision_conops(source,semantic_session_id):
    """Cloud staging preserves existing typed ConOps requirement, never heals."""
    from vmodel_contracts import ConOpsDocument,check_compatible
    conops=source.get('conops') if isinstance(source,dict) else None
    if not isinstance(conops,dict):raise ValueError('cloud revision staging requires typed source ConOps')
    check_compatible(conops)
    typed=ConOpsDocument.model_validate(conops)
    if typed.session_id!=semantic_session_id:raise ValueError('cloud ConOps semantic source identity mismatch')
    return typed
