"""Private isolated staging preparation, no promotion/install or model calls.

Trusted caller supplies immutable held lineage/options/review from owner store.
Original source writer fence must already be held; no client path grants access.
Partial failure retains private bytes for explicit review, never auto cleanup.
"""
import hashlib,json,os,sqlite3,re
from pathlib import Path
from copy import deepcopy
from .tenancy import session_root
from .producer_inputs import _open_directory,_identity
from .revision_stage_evidence import read_approved_upstream_documents,read_validated_staged_receipt,validate_cloud_revision_conops
from .revision_source_review import read_source_review_snapshot,source_review_payload
from .revision_proposal import read_retained_revision_proposal


def prepare_private_revision_stage(store,capability,*,artifacts_base,lineage,lineage_sha256,options,review_snapshot):
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import check_execution,guarded_publish
    from spec_generator.revision_source import read_closed_checkpoint
    from spec_generator.revision_checkpoint import prepare_revision_seed,stage_private_lineage_checkpoint
    from spec_generator.graph import build_graph,checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver
    from types import SimpleNamespace
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
    source=read_closed_checkpoint(root/'spec'/'checkpoints.sqlite',session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
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
    upstream=read_approved_upstream_documents(root,expected_documents=expected)
    if {name:hashlib.sha256(raw).hexdigest() for name,raw in upstream.items()}!=review_snapshot['snapshot']['upstream_sha256']:
        raise ValueError('upstream byte inventory differs from retained review')
    prepared=prepare_revision_seed(source=source,phase=lineage['phase'],change_request=options['change_request'],session_id=lineage['semantic_session_id'])
    result=deepcopy(prepared['seed']);result['gate_feedback'].pop(lineage['phase'],None)
    result[lineage['phase']]=deepcopy(proposal['proposal']['artifact'])
    parent=root/'spec'/'revision-proposals'/lineage['proposal_id']
    pfd=_open_directory(parent);fds=[]
    try:
        def mkdir(name,fd,*,fresh=False):
            def create():
                try:os.mkdir(name,mode=0o700,dir_fd=fd)
                except FileExistsError:
                    if fresh:raise ValueError('private stage already exists; explicit reconciliation required')
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.fsync(fd);fds.append(child);return child
            return guarded_publish(create)
        staged=mkdir('staged',pfd);destination=mkdir(lineage['execution_session_id'],staged,fresh=True)
        spec=mkdir('spec',destination,fresh=True);interview=mkdir('interview',destination,fresh=True)
        def write(fd,name,raw):
            def publish():
                file=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    with os.fdopen(file,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(file)
                finally:os.close(file)
                os.fsync(fd)
            authority();guarded_publish(publish)
        for name,raw in upstream.items():
            directory,filename=name.split('/');write(interview if directory=='interview' else spec,filename,raw)
        write(spec,lineage['phase']+'.json',json.dumps(result[lineage['phase']],sort_keys=True,indent=2).encode())
        def checkpoint():
            # Exclusive empty file and pinned directory path prevent a namespace
            # swap redirecting SQLite writes. No source or active run is opened RW.
            fd=os.open('checkpoints.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=spec);os.close(fd)
            return sqlite3.connect(f'/proc/self/fd/{spec}/checkpoints.sqlite')
        authority();conn=guarded_publish(checkpoint)
        try:
            from spec_generator.stop_publication import FencedCheckpointer,publication_guard
            saver=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(binding[0],binding[1]))
            graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=saver)
            receipt=stage_private_lineage_checkpoint(source=source,proposal_result=result,lineage=lineage,
                change_request=options['change_request'],graph=graph,config={'configurable':{'thread_id':lineage['execution_session_id']}},validate_authority=authority)
        finally:conn.close()
        fd=os.open('checkpoints.sqlite',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=spec)
        try:os.fsync(fd)
        finally:os.close(fd)
        os.fsync(spec)
        dest=parent/'staged'/lineage['execution_session_id']
        current=_open_directory(dest)
        try:
            if _identity(current)!=_identity(destination):raise ValueError('private stage namespace changed')
        finally:os.close(current)
        names=list(upstream)+['spec/'+lineage['phase']+'.json','spec/checkpoints.sqlite']
        files={}
        for name in names:
            directory,filename=name.split('/');fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=interview if directory=='interview' else spec)
            try:
                with os.fdopen(fd,'rb',closefd=False) as stream:files[name]=hashlib.file_digest(stream,'sha256').hexdigest()
            finally:os.close(fd)
        manifest={'kind':'closed_revision_staged_manifest','lineage_sha256':lineage_sha256,
            'checkpoint_id':receipt['destination_checkpoint']['configurable']['checkpoint_id'],'files':files}
        raw=json.dumps(manifest,sort_keys=True,indent=2).encode();write(destination,'manifest.json',raw)
        authority()
        if read_retained_revision_proposal(store,capability,root,lineage['proposal_id'],artifacts_base=artifacts_base,options=options)!=proposal:
            raise ValueError('retained proposal changed during staging')
        final=read_validated_staged_receipt(store,capability,artifacts_base=artifacts_base,lineage=lineage,
            lineage_sha256=lineage_sha256,manifest_sha256=hashlib.sha256(raw).hexdigest(),options=options)
        return final|{'installed':False,'dispatch_started':False,'source_mutated':False}
    finally:
        for fd in reversed(fds):os.close(fd)
        os.close(pfd)
