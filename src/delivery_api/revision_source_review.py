"""Immutable upstream-byte review snapshot; no acceptance/install authority."""
import hashlib,json,re
from copy import deepcopy


def source_review_payload(*,lineage_sha256,checkpoint_id,semantic_session_id,phase,upstream_sha256):
    if phase not in ('srs','architecture'):raise ValueError('implemented source review phase required')
    if not isinstance(lineage_sha256,str) or not re.fullmatch('[a-f0-9]{64}',lineage_sha256):raise ValueError('exact lineage digest required')
    if not isinstance(checkpoint_id,str) or not 1<=len(checkpoint_id)<=200:raise ValueError('exact source checkpoint required')
    if not isinstance(semantic_session_id,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}',semantic_session_id):raise ValueError('canonical semantic identity required')
    required={'interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json'}
    if phase=='architecture':required.add('spec/srs.json')
    if not isinstance(upstream_sha256,dict) or set(upstream_sha256)!=required:raise ValueError('complete source review inventory required')
    if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in upstream_sha256.values()):raise ValueError('exact upstream review digests required')
    body={'kind':'closed_revision_upstream_review_snapshot','lineage_sha256':lineage_sha256,
        'source_checkpoint_id':checkpoint_id,'semantic_session_id':semantic_session_id,'phase':phase,
        'upstream_sha256':deepcopy(upstream_sha256),'state':'awaiting_review','accepted':False,'installed':False}
    return {'snapshot':body,'snapshot_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()}


def read_source_review_snapshot(store,capability,*,artifacts_base,lineage,lineage_sha256):
    """Caller holds same-host source writer exclusion; never accepts a review."""
    from pathlib import Path
    from .tenancy import session_root
    from .revision_stage_evidence import read_approved_upstream_bytes,validate_cloud_revision_conops
    from spec_generator.revision_source import read_closed_checkpoint
    capability.validate(store)
    if (hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=lineage_sha256
        or any(lineage.get(k)!=v for k,v in (('owner_id',capability.scope.owner_id),('task_id',capability.task_id),
            ('parent_execution_session_id',capability.session_id),('parent_generation',capability.generation),
            ('state','planned'),('installed',False),('dispatch_started',False)))):
        raise ValueError('review lineage differs from current parent')
    if (lineage.get('installed') is not False or lineage.get('dispatch_started') is not False
        or type(lineage.get('generation')) is not int or lineage['generation']!=capability.generation+1):
        raise ValueError('exact uninstalled next generation required')
    root=session_root(Path(artifacts_base),capability.scope,capability.session_id)
    source=read_closed_checkpoint(root/'spec'/'checkpoints.sqlite',session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
    validate_cloud_revision_conops(source,lineage['semantic_session_id'])
    documents={'interview/intent_model.json':source.get('intent_model'),'interview/conops.json':source.get('conops'),
        'spec/needs.json':source.get('needs'),'spec/system-baseline.json':source.get('system_baseline')}
    if lineage['phase']=='architecture':documents['spec/srs.json']=source.get('srs')
    digests=read_approved_upstream_bytes(root,expected_documents=documents)
    if read_closed_checkpoint(root/'spec'/'checkpoints.sqlite',session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])!=source:
        raise ValueError('source checkpoint changed across review snapshot')
    capability.validate(store)
    return source_review_payload(lineage_sha256=lineage_sha256,checkpoint_id=lineage['source_checkpoint_id'],
        semantic_session_id=lineage['semantic_session_id'],phase=lineage['phase'],upstream_sha256=digests)
