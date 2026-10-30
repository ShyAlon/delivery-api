"""Direct durable PG artifacts: no publish/read filesystem bridge."""
import hashlib,uuid
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context


def test_publish_bytes_builds_same_ready_manifest_without_opening_file(store,monkeypatch):
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('byte-artifacts-'+uuid.uuid4().hex)['task_id']
        data=b'{"source":"original exact bytes"}\n'
        report={'validator':'test-only','validator_version':'test-only','valid':True,'error':None,'sha256':hashlib.sha256(data).hexdigest()}
        monkeypatch.setattr(Path,'read_bytes',lambda *a:(_ for _ in ()).throw(AssertionError('filesystem forbidden')))
        rows=store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':data,'validation_report':report}],writer_instance='test-only')
        assert len(rows)==1 and rows[0]['sha256']==report['sha256'] and rows[0]['byte_size']==len(data)
        assert store.manifest_ready(tid) and store.read_artifact_bytes(tid,'srs','srs.json')==data
        snapshot=store.manifest_snapshot(tid)
        assert snapshot['rows'][0]['ref'].startswith('pg-artifact:')
        with tenant_context(B):
            assert store.manifest_snapshot(tid) is None
            with pytest.raises(KeyError):store.read_artifact_bytes(tid,'srs','srs.json')


def test_publish_bytes_validation_failure_rolls_back_whole_revision(store):
    with tenant_context(A):
        tid=store.create_task('byte-rollback-'+uuid.uuid4().hex)['task_id'];original=b'{"original":true}'
        store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':original}],writer_instance='test-only')
        before=store.manifest_snapshot(tid)
        with pytest.raises(ValueError,match='hash'):
            store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':b'{"replacement":true}'},
                {'phase_key':'architecture','name':'architecture.json','data':b'{}','validation_report':{'sha256':'f'*64,'validator_version':'test-only'}}],writer_instance='test-only')
        assert store.manifest_snapshot(tid)==before and store.read_artifact_bytes(tid,'srs','srs.json')==original


@pytest.mark.parametrize('data',[None,'text',bytearray(b'{}')])
def test_publish_bytes_requires_actual_bounded_bytes(store,data):
    with tenant_context(A):
        tid=store.create_task('byte-bound-'+uuid.uuid4().hex)['task_id']
        with pytest.raises(ValueError,match='bytes'):
            store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':data}],writer_instance='test-only')
        assert store.manifest_snapshot(tid) is None


def test_revision_phase_evidence_uses_exact_owned_pg_manifest_without_files(store,tmp_path):
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.revision_evidence import read_revision_artifacts
    from delivery_api.tenancy import session_root
    with tenant_context(A):
        tid=store.create_task('byte-evidence-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'byte-'+uuid.uuid4().hex)
        cap=WorkerCapability.capture(store,tid);data=b'{"exact":"reviewed"}\n'
        store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':data}],writer_instance='test-only')
        root=session_root(tmp_path,A,cap.session_id)
        assert read_revision_artifacts(store,cap,root,'srs',artifacts_base=tmp_path)==[
            {'phase':'srs','name':'srs.json','generation':1,'sha256':hashlib.sha256(data).hexdigest()}]
        assert not root.exists()


def test_fresh_generator_inputs_are_pg_owned_and_need_no_local_files(store,tmp_path):
    import json
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.producer_inputs import read_fresh_inputs
    from delivery_api.tenancy import session_root
    with tenant_context(A):
        tid=store.create_task('byte-input-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'byte-'+uuid.uuid4().hex)
        cap=WorkerCapability.capture(store,tid)
        expected={'intent':{'session_id':cap.session_id,'input':'intent'},'conops':{'session_id':cap.session_id,'input':'confirmed-conops'},'baseline':{'input':'reviewed-baseline'}}
        store.publish_task_revision_bytes(tid,[{'phase_key':phase,'name':name,'data':json.dumps(expected[key]).encode()} for phase,name,key in
            [('intent','intent_model.json','intent'),('conops','conops.json','conops'),('baseline','system-baseline.json','baseline')]],writer_instance='test-only')
        root=session_root(tmp_path,A,cap.session_id)
        assert read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)==expected
        assert not root.exists()
        with tenant_context(B):
            with pytest.raises(PermissionError):read_fresh_inputs(store,cap,root,artifacts_base=tmp_path)
