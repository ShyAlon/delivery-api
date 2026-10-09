"""Pinned private staging manifest readback, not promotion or installation.

Caller owns same-host source/destination writer exclusion. Manifest digest and
lineage must come from the held control-plane plan, not client-supplied paths.
"""
import hashlib,json,os,re,stat
from pathlib import Path
from .producer_inputs import _open_directory,_identity,_content_stamp,MAX_DOCUMENT_BYTES
from .tenancy import session_root,TenantAccessDenied


def read_staged_revision_manifest(store,capability,*,artifacts_base,lineage,lineage_sha256,manifest_sha256,expected_documents=None,expected_upstream_sha256=None,promoted=False):
    capability.validate(store)
    if not isinstance(lineage,dict) or hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=lineage_sha256:
        raise ValueError('exact held lineage digest required')
    if any(lineage.get(k)!=v for k,v in (('owner_id',capability.scope.owner_id),('task_id',capability.task_id),
        ('parent_execution_session_id',capability.session_id),('parent_generation',capability.generation),('state','planned'),('installed',False),('dispatch_started',False))):
        raise TenantAccessDenied('staging lineage differs from captured parent')
    if (lineage.get('installed') is not False or lineage.get('dispatch_started') is not False
        or type(lineage.get('generation')) is not int or lineage['generation']!=capability.generation+1):
        raise ValueError('exact uninstalled next generation required')
    pid=lineage.get('proposal_id');sid=lineage.get('execution_session_id');phase=lineage.get('phase')
    if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{32}',pid) or not isinstance(sid,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}',sid):
        raise ValueError('canonical private staging identities required')
    if phase not in ('srs','architecture') or sid==capability.session_id or sid==lineage.get('semantic_session_id'):
        raise ValueError('fresh implemented execution staging required')
    if not isinstance(manifest_sha256,str) or not re.fullmatch('[a-f0-9]{64}',manifest_sha256):raise ValueError('exact reviewed staged manifest digest required')
    root=session_root(Path(artifacts_base),capability.scope,capability.session_id)/'spec'/'revision-proposals'/pid/'staged'/sid
    if promoted is True:root=session_root(Path(artifacts_base),capability.scope,sid)
    elif promoted is not False:raise ValueError('exact private promoted reader mode required')
    parent=_open_directory(root);records=[]
    try:
        def read(relative,limit):
            parts=relative.split('/');directory=os.dup(parent)
            try:
                for part in parts[:-1]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);os.close(directory);directory=child
                fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                try:
                    before=os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size>limit:raise ValueError('bounded regular staged file required')
                    with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
                    if len(raw)>limit or _content_stamp(os.fstat(fd))!=_content_stamp(before) or _content_stamp(os.stat(parts[-1],dir_fd=directory,follow_symlinks=False))!=_content_stamp(before):
                        raise TenantAccessDenied('staged content changed during read')
                    records.append((relative,_content_stamp(before)));return raw
                finally:os.close(fd)
            finally:os.close(directory)
        raw=read('manifest.json',MAX_DOCUMENT_BYTES)
        if hashlib.sha256(raw).hexdigest()!=manifest_sha256:raise ValueError('staged manifest differs from reviewed digest')
        manifest=json.loads(raw)
        if not isinstance(manifest,dict) or set(manifest)!={'kind','lineage_sha256','checkpoint_id','files'} or manifest['kind']!='closed_revision_staged_manifest' or manifest['lineage_sha256']!=lineage_sha256:
            raise ValueError('exact lineage staging manifest required')
        if not isinstance(manifest['checkpoint_id'],str) or not 1<=len(manifest['checkpoint_id'])<=200:raise ValueError('exact staged checkpoint identity required')
        files=manifest['files'];required={'interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json','spec/checkpoints.sqlite','spec/'+phase+'.json'}
        if phase=='architecture':required.add('spec/srs.json')
        if not isinstance(files,dict) or set(files)!=required:raise ValueError('exact upstream and edited-phase staging inventory required')
        if expected_documents is not None and set(expected_documents)!=required-{'spec/checkpoints.sqlite'}:
            raise ValueError('complete expected checkpoint documents required')
        if expected_upstream_sha256 is not None and not set(expected_upstream_sha256)<=required:
            raise ValueError('canonical upstream digest inventory required')
        for name,digest in files.items():
            if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('exact staged file digest required')
            if expected_upstream_sha256 is not None and name in expected_upstream_sha256 and digest!=expected_upstream_sha256[name]:
                raise ValueError('staged upstream bytes differ from approved source files')
            limit=64*1024*1024 if name.endswith('.sqlite') else MAX_DOCUMENT_BYTES
            content=read(name,limit)
            if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('staged file digest mismatch')
            if expected_documents is not None and name in expected_documents and json.loads(content)!=expected_documents[name]:
                raise ValueError('staged document differs from validated checkpoint channel')
        spec=os.open('spec',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            for name in ('checkpoints.sqlite-wal','checkpoints.sqlite-shm','checkpoints.sqlite-journal'):
                try:os.stat(name,dir_fd=spec,follow_symlinks=False)
                except FileNotFoundError:continue
                raise ValueError('staged checkpoint requires closed sidecar-free snapshot')
        finally:os.close(spec)
        # Re-open all contained paths after the whole inventory, to detect early
        # file/parent swaps. These comparisons do not replace writer exclusion.
        current=_open_directory(root)
        try:
            if _identity(current)!=_identity(parent):raise TenantAccessDenied('staged root namespace changed')
            for name,stamp in records:
                directory=os.dup(current);parts=name.split('/')
                try:
                    for part in parts[:-1]:
                        child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);os.close(directory);directory=child
                    if _content_stamp(os.stat(parts[-1],dir_fd=directory,follow_symlinks=False))!=stamp:raise TenantAccessDenied('staged inventory changed across readback')
                finally:os.close(directory)
        finally:os.close(current)
        capability.validate(store)
        return {'manifest_sha256':manifest_sha256,'checkpoint_id':manifest['checkpoint_id']}
    finally:os.close(parent)


def read_validated_staged_receipt(store,capability,*,artifacts_base,lineage,lineage_sha256,manifest_sha256,options,promoted=False):
    """Compose pinned bytes, exact SQLite gate and typed semantic readback.

    Caller derives lineage/options from immutable held records and owns writer
    exclusion. This validates staging only, not owner approval or installation.
    """
    from .closed_revision_job import _options
    from spec_generator.revision_source import read_closed_checkpoint
    from spec_generator.revision_checkpoint import validate_staged_lineage_state
    options=_options(options)
    if (options['phase']!=lineage.get('phase') or options['source_checkpoint_id']!=lineage.get('source_checkpoint_id')
        or options['preflight_digest']!=lineage.get('review_digest')
        or options['artifact_evidence']!=lineage.get('approved_source_artifact_evidence')):
        raise ValueError('staged options differ from held lineage')
    arguments=dict(store=store,capability=capability,artifacts_base=artifacts_base,lineage=lineage,
        lineage_sha256=lineage_sha256,manifest_sha256=manifest_sha256,promoted=promoted)
    before=read_staged_revision_manifest(**arguments)
    root=session_root(Path(artifacts_base),capability.scope,capability.session_id)
    source=read_closed_checkpoint(root/'spec'/'checkpoints.sqlite',session_id=capability.session_id,
        expected_checkpoint_id=lineage['source_checkpoint_id'])
    validate_cloud_revision_conops(source,lineage['semantic_session_id'])
    staged_path=root/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id']/'spec'/'checkpoints.sqlite'
    if promoted is True:staged_path=session_root(Path(artifacts_base),capability.scope,lineage['execution_session_id'])/'spec'/'checkpoints.sqlite'
    staged=read_closed_checkpoint(staged_path,session_id=lineage['execution_session_id'],
        expected_checkpoint_id=before['checkpoint_id'],expected_review_phase=lineage['phase'])
    validate_staged_lineage_state(source=source,staged_state=staged,lineage=lineage,change_request=options['change_request'])
    # Reject edits to original phase artifacts and the source checkpoint observed
    # over validation. Same-host exclusion is still required for commit safety.
    from .revision_evidence import read_revision_artifacts
    evidence=read_revision_artifacts(store,capability,root,lineage['phase'],artifacts_base=artifacts_base)
    if evidence!=options['artifact_evidence']:raise ValueError('reviewed source artifacts changed before staged readiness')
    if read_closed_checkpoint(root/'spec'/'checkpoints.sqlite',session_id=capability.session_id,
        expected_checkpoint_id=lineage['source_checkpoint_id'])!=source:
        raise ValueError('source checkpoint changed across staged validation')
    documents={'interview/intent_model.json':source.get('intent_model'),'interview/conops.json':source.get('conops'),
        'spec/needs.json':source.get('needs'),'spec/system-baseline.json':source.get('system_baseline'),
        'spec/'+lineage['phase']+'.json':staged.get(lineage['phase'])}
    if lineage['phase']=='architecture':documents['spec/srs.json']=source.get('srs')
    upstream={name:value for name,value in documents.items() if name!='spec/'+lineage['phase']+'.json'}
    upstream_digests=read_approved_upstream_bytes(root,expected_documents=upstream)
    after=read_staged_revision_manifest(**arguments,expected_documents=documents,expected_upstream_sha256=upstream_digests)
    if read_approved_upstream_bytes(root,expected_documents=upstream)!=upstream_digests:
        raise ValueError('approved upstream files changed across staging readback')
    if after!=before:raise ValueError('staged receipt changed across typed readback')
    capability.validate(store)
    return after


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
