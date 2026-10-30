"""Exact generation-scoped PG artifacts. No file fallback or content restamp."""
import hashlib,json
from .producer_inputs import MAX_DOCUMENT_BYTES


def documents(store,capability,expected):
    capability.validate(store);result={}
    snapshot=store.manifest_snapshot(capability.task_id,capability.generation)
    if snapshot is None:raise ValueError('owned ready artifact manifest required')
    rows={(row['phase_key'],row['name']):row for row in snapshot['rows']}
    for path,value in expected.items():
        directory,name=path.split('/')
        phase={'intent_model.json':'intent','conops.json':'conops','needs.json':'needs','system-baseline.json':'baseline','srs.json':'srs'}.get(name)
        if phase is None:raise ValueError('canonical upstream artifact required')
        row=rows.get((phase,name))
        if row is None:raise ValueError('owned upstream artifact missing')
        raw=store.read_artifact_bytes_sha(row['sha256'])
        if len(raw)>MAX_DOCUMENT_BYTES or json.loads(raw)!=value:raise ValueError('upstream artifact differs from checkpoint')
        result[path]=raw
    capability.validate(store);return result


def phase_evidence(store,capability,phase):
    from .phases import PHASE_BY_KEY
    capability.validate(store)
    snapshot=store.manifest_snapshot(capability.task_id,capability.generation)
    if snapshot is None:raise ValueError('owned ready artifact manifest required')
    members={row['name']:row for row in snapshot['rows'] if row['phase_key']==phase}
    result=[]
    for name in PHASE_BY_KEY[phase].artifacts:
        row=members.get(name)
        if row is None:
            if name in PHASE_BY_KEY[phase].conditional_artifacts:continue
            raise ValueError('owned required phase artifact missing')
        raw=store.read_artifact_bytes_sha(row['sha256'])
        if len(raw)>MAX_DOCUMENT_BYTES:raise ValueError('bounded phase artifact required')
        result.append({'phase':phase,'name':name,'generation':capability.generation,'sha256':hashlib.sha256(raw).hexdigest()})
    capability.validate(store);return result
