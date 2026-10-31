"""Zero-model export from one pinned PG revision. No mutable document fallback."""
import hashlib,io,json,tempfile,zipfile
from pathlib import Path
from .final_package import PackageBlocked,package

MAX_ZIP=24_000_000

def verify(raw,*,package_id,owner_id,task_id,generation,revision_id,revision_hash,session_id):
    if not raw or len(raw)>MAX_ZIP:raise PackageBlocked('Invalid package size')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or sum(i.file_size for i in infos)>22_000_000:raise ValueError('bounded unique members required')
            manifest=json.loads(archive.read('package.json'))
            identity=manifest.pop('package_id')
            if identity!=package_id or hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()!=identity:raise ValueError('identity differs')
            expected={'owner_id':owner_id,'revision_id':revision_id,'revision_hash':revision_hash}
            if (manifest['source']!=expected or manifest['task_id']!=task_id or manifest['generation']!=generation or manifest['session_id']!=session_id):raise ValueError('binding differs')
            if set(names)!=set(manifest['sha256'])|{'package.json'}:raise ValueError('members differ')
            files={}
            for name,digest in manifest['sha256'].items():
                data=archive.read(name)
                if len(data)>2_000_000 or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('checksum differs')
                files[name]=data.decode()
            # Reuse the reviewed allowlist and bounds, not merely a matching hash.
            task={'task_id':task_id,'generation':generation,'state':'done','phases':[]}
            with tempfile.TemporaryDirectory(prefix='requi-package-verify-') as tmp:
                package(task,session_id,{'session_id':session_id,'files':files,'sha256':manifest['sha256'],'blocked_reasons':[]},Path(tmp))
            return sorted(files)
    except (ValueError,KeyError,zipfile.BadZipFile,UnicodeError) as exc:
        raise PackageBlocked('Stored package verification failed') from exc


def build(store,task,snapshot,owner_id):
    from .artifact_publication import document_artifact_keys,handoff_document_keys
    from .final_package import producer_handoff
    keys=document_artifact_keys()|handoff_document_keys()|{('baseline','system-baseline.json'):'spec/system-baseline.json'}
    members={(r['phase_key'],r['name']):r for r in snapshot['rows']}
    required={'interview/intent_model.json','interview/conops.json','spec/needs.json','spec/srs.json','spec/architecture.json','spec/items.json','spec/validation.json','spec/findings-ledger.json','spec/system-baseline.json'}
    captured={};total=0
    for identity,path in keys.items():
        row=members.get(identity)
        if row is None:continue
        if row.get('valid') is False:raise PackageBlocked('Published phase validation failed')
        raw=store.read_artifact_bytes_sha(row['sha256']);total+=len(raw)
        if len(raw)>2_000_000 or total>20_000_000:raise PackageBlocked('Published inputs exceed export limits')
        captured[path]=raw
    if not required.issubset(captured) or not {'spec/tests.json','spec/test_specifications.json'}&set(captured):raise PackageBlocked('Final revision lacks required export evidence')
    source={'owner_id':owner_id,'revision_id':snapshot['revision_id'],'revision_hash':snapshot['revision_hash']}
    with tempfile.TemporaryDirectory(prefix='requi-final-export-') as tmp:
        root=Path(tmp)
        for path,raw in captured.items():
            dest=root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        handoff=producer_handoff(root/'interview',root/'spec',root/'spec/system-baseline.json')
        result=package(task,task['engine_session_id'],handoff,root/'packages',source=source)
        raw=result['path'].read_bytes()
    verify(raw,package_id=result['package_id'],owner_id=owner_id,task_id=task['task_id'],generation=task['generation'],session_id=task['engine_session_id'],**{k:source[k] for k in ('revision_id','revision_hash')})
    return result['package_id'],raw,result['contents']
