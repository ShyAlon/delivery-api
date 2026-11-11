import json
from delivery_api.answer_diagnostics import exported_json


def test_local_recovery_export_decodes_before_secret_redaction(tmp_path,monkeypatch):
    from interview_engine.document_diagnostics import answer_batch,bind,append
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    root=tmp_path/'tenants/owner/sessions/.diagnostic-recovery'/('a'*64)
    class Docs:
        def diagnostic_many(self,ops):raise RuntimeError('test unavailable')
    with answer_batch(root,{'owner_id':'owner','operation_id':'op'}):
        with bind(Docs(),tmp_path/'session'):
            append(tmp_path/'session/interview/debug-http.jsonl','API_KEY=test-only-private-key\nproject evidence\n')
    files=list(root.glob('*.json'));before=files[0].read_bytes()
    text=exported_json();value=json.loads(text)
    assert 'test-only-private-key' not in text
    assert 'project evidence' in text
    assert value['kind']=='diagnostic-only-not-execution-replay'
    assert len(value['records'])==1
    assert files[0].read_bytes()==before,'debug export must not consume recovery evidence'
