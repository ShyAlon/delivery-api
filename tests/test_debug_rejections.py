"""Owned, bounded rejection evidence, never arbitrary manifest-selected docs."""
import io,json,zipfile
from delivery_api.debug_export import diagnostic_zip


def export(tmp_path,docs):
    reads=[]
    def read(key):reads.append(key);return docs.get(key)
    raw=diagnostic_zip(tmp_path/'session','task','session',1,{}, {},[],tmp_path/'absent',document_reader=read)
    return zipfile.ZipFile(io.BytesIO(raw)),reads


def test_real_runtime_root_rejections_include_reason_raw_and_source(tmp_path):
    name='architecture--ADR-1-decide-stack--attempt-2--cite.raw.txt'
    manifest={'path':str(tmp_path/'session'/'rejected-llm-outputs'/name),'stage':'architecture','item':'ADR-1','attempt':2,'reason':'cite recorded decision'}
    docs={'rejected-llm-outputs/manifest.jsonl':(json.dumps(manifest)+'\n').encode(),
          'rejected-llm-outputs/'+name:b'{"decision":"Original project wording", "api_key":"private-secret"}',
          'interview/intent_model.json':b'{"founder_register":{"items":[{"item_id":"FD-empty","decision":"Exact source"}]}}'}
    z,reads=export(tmp_path,docs)
    assert 'rejected-llm-outputs/manifest.jsonl' in z.namelist()
    assert b'cite recorded decision' in z.read('rejected-llm-outputs/manifest.jsonl')
    assert b'Original project wording' in z.read('rejected-llm-outputs/'+name)
    assert b'private-secret' not in z.read('rejected-llm-outputs/'+name)
    assert b'Exact source' in z.read('interview/intent_model.json')


def test_rejection_manifest_cannot_select_outside_paths_or_other_documents(tmp_path):
    bad=['/outside/rejected-llm-outputs/architecture--bad.raw.txt',str(tmp_path/'session'/'interview'/'intent_model.json'),
         str(tmp_path/'session'/'rejected-llm-outputs'/'..'/'private.raw.txt'),'../other/architecture--bad.raw.txt',
         str(tmp_path/'session'/'rejected-llm-outputs'/'nested'/'architecture--bad.raw.txt')]
    docs={'rejected-llm-outputs/manifest.jsonl':('\n'.join(json.dumps({'path':p}) for p in bad)+'\n').encode()}
    z,reads=export(tmp_path,docs)
    assert not any(key.endswith('.raw.txt') for key in reads)
    assert any(o['reason']=='invalid rejection output key' for o in json.loads(z.read('manifest.json'))['omissions'])


def test_only_latest_eight_rejected_outputs_and_size_limits(tmp_path):
    docs={};rows=[]
    for i in range(12):
        name=f'architecture--ADR-{i}--attempt-2--invalid.raw.txt'
        rows.append({'path':str(tmp_path/'session'/'rejected-llm-outputs'/name)})
        docs['rejected-llm-outputs/'+name]=b'x'*(2_000_001 if i==11 else 10)
    docs['rejected-llm-outputs/manifest.jsonl']=('\n'.join(json.dumps(r) for r in rows)+'\n').encode()
    z,reads=export(tmp_path,docs)
    assert sum(key.endswith('.raw.txt') for key in reads)==8
    assert sum(key.endswith('.raw.txt') for key in z.namelist())==7
    omissions=json.loads(z.read('manifest.json'))['omissions']
    assert any(o['reason']=='older rejection outputs omitted; latest 8 only' for o in omissions)
    assert any(o['reason']=='source over 2 MB limit' for o in omissions)


def test_legacy_spec_rejections_work_without_filesystem_and_share_global_limit(tmp_path):
    docs={}
    for area,count in [('rejected-llm-outputs',5),('spec/rejected-llm-outputs',6)]:
        rows=[]
        for i in range(count):
            name=f'architecture--ADR-{i}--attempt-2--cite.raw.txt'
            rows.append({'path':area+'/'+name})
            docs[area+'/'+name]=b'Project-only evidence'
        docs[area+'/manifest.jsonl']=('\n'.join(json.dumps(r) for r in rows)+'\n').encode()
    z,reads=export(tmp_path,docs)
    assert sum(key.endswith('.raw.txt') for key in reads)==8
    assert sum(key.endswith('.raw.txt') for key in z.namelist())==8
    assert sum(key.startswith('spec/') and key.endswith('.raw.txt') for key in z.namelist())==3
