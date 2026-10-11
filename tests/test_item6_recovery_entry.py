import sys,json,importlib.util
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_entry


def test_missing_exact_recovery_review_rejected_before_database(monkeypatch,tmp_path):
    original=tmp_path/'dump';original.write_bytes(b'fixture')
    identity=tmp_path/'identity.json';identity.write_text(json.dumps({'stage':'identity','source_mode':'mac_acceptance','signature_verified':True,
        'subject':'045bafdf-e35b-4b4b-93f2-0cd981b52806','session_id':'16caab89-5c55-4c36-af25-ba65105ed74f','run_id':'4eda39c0684945ff849b582c60dd91f8'}))
    calls=[];monkeypatch.setattr(recovery_entry.subprocess,'run',lambda *a,**kw:calls.append(a))
    monkeypatch.setattr(sys,'argv',['recovery','--dump',str(original),'--identity',str(identity),'--expected-dump-sha256',__import__('hashlib').sha256(b'fixture').hexdigest(),'--attempt','a1c69b8a218c49d2829dff112d0a2688','--output',str(tmp_path/'new'),
        '--approve-plan','a'*64])
    with pytest.raises(ValueError,match='review'):recovery_entry.main()
    assert calls==[] and not(tmp_path/'new').exists()


def test_changed_paid_review_rejected_before_database(monkeypatch,tmp_path):
    original=tmp_path/'dump';original.write_bytes(b'fixture')
    identity=tmp_path/'identity.json';identity.write_text(json.dumps({'stage':'identity','source_mode':'mac_acceptance','signature_verified':True,
        'subject':'045bafdf-e35b-4b4b-93f2-0cd981b52806','session_id':'16caab89-5c55-4c36-af25-ba65105ed74f','run_id':'4eda39c0684945ff849b582c60dd91f8'}))
    review=tmp_path/'review';review.write_text('{}')
    calls=[];monkeypatch.setattr(recovery_entry.subprocess,'run',lambda *a,**kw:calls.append(a))
    monkeypatch.setattr(sys,'argv',['recovery','--dump',str(original),'--identity',str(identity),'--expected-dump-sha256',__import__('hashlib').sha256(b'fixture').hexdigest(),'--attempt','a1c69b8a218c49d2829dff112d0a2688','--output',str(tmp_path/'new'),
        '--continue-reviewed-sha256','a'*64,'--continuation-review',str(review),'--source-run',str(tmp_path),'--runtime',str(tmp_path)])
    with pytest.raises(ValueError,match='review'):recovery_entry.main()
    assert calls==[] and not(tmp_path/'new').exists()


def test_input_tree_binding_includes_wal_and_runtime(tmp_path):
    from recovery_manifest import prepare_review,tree_digest
    source=tmp_path/'source';source.mkdir();(source/'checkpoints.sqlite').write_bytes(b'main')
    (source/'checkpoints.sqlite-wal').write_bytes(b'wal')
    runtime=tmp_path/'runtime';runtime.mkdir();(runtime/'catalog.json').write_text('{}')
    dump=tmp_path/'dump';dump.write_bytes(b'dump')
    review,digest=prepare_review(run_id='a'*32,dump=dump,source_run=source,runtime=runtime,maximum_expense_units=1000)
    assert review['new_purchase'] is False and review['uninterrupted_acceptance'] is False
    before=tree_digest(source);(source/'checkpoints.sqlite-wal').write_bytes(b'changed')
    assert tree_digest(source)!=before
    link=source/'link';link.symlink_to(dump)
    with pytest.raises(ValueError):tree_digest(source)
