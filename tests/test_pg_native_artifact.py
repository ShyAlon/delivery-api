"""Manifest and producer SQL must bypass the dialect bridge, keeping byte authority."""
import hashlib
from uuid import uuid4
import pytest
from delivery_api.tenancy import tenant_context


def test_native_manifest_and_producer_consumers(pg_workspace_factory,tmp_path,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store
        with tenant_context(a.scope):
            tid=s.create_task('Native publication')['task_id'];s.set_engine_session(tid,'native-pub-'+uuid4().hex)
            job=s.enqueue_worker(tid,'fake-publication')
        original=_Connection.execute
        def reject(self,sql,parameters=()):
            if any(x in sql for x in ('artifact_bytes','artifact_manifest_ready','producer_results','provider_attempts','task_artifacts')) and not sql.startswith('\nSELECT t.*'):
                raise AssertionError('artifact/producer SQL still consumes dialect bridge')
            return original(self,sql,parameters)
        path=tmp_path/'srs.json';path.write_bytes(b'{"native":true}')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        args=[dict(phase_key='spec',name='srs.json',ref=str(path),validation_report=dict(sha256=digest,validator='fixture',validator_version='1',valid=True,error=None))]
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            with tenant_context(a.scope):
                assert s.manifest_snapshot(tid) is None
                s.publish_task_revision(tid,args,writer_instance='native')
                snap=s.manifest_snapshot(tid);assert len(snap['rows'])==1
                assert s.manifest_revision(tid)==(snap['revision_id'],snap['revision_hash'])
                assert s.manifest_ready(tid)
                assert len(s.manifest_artifacts(tid))==1
                assert s.read_artifact_bytes(tid,'spec','srs.json')==path.read_bytes()
                assert s.read_artifact_bytes_sha(digest)==path.read_bytes()
                s.record_producer_result(job,tid,1,str(tmp_path),digest)
                assert s.producer_result(job)['content_hash']==digest
                assert s.producer_paid_evidence(job)==0
                s.mark_producer_published(job)
                with pytest.raises(ValueError,match='already marked'):s.mark_producer_published(job)
                with patch.context() as fault:
                    def fail(*args,**kw):raise RuntimeError('readiness fault')
                    fault.setattr(s,'_mark_revision_ready',fail)
                    path.write_bytes(b'{"replacement":true}')
                    replacement=[dict(phase_key='spec',name='other.json',ref=str(path))]
                    with pytest.raises(RuntimeError,match='readiness fault'):s.publish_task_revision(tid,replacement,writer_instance='fault')
                assert s.manifest_snapshot(tid)==snap
                assert len(s.manifest_artifacts(tid))==1
                assert s.read_artifact_bytes_sha(digest)==b'{"native":true}'
                s.publish_task_revision(tid,replacement,writer_instance='next')
                assert [r['name'] for r in s.manifest_snapshot(tid)['rows']]==['other.json']
                assert sorted(r['publication_state'] for r in s.manifest_artifacts(tid))==['published','superseded']
                with pytest.raises(KeyError):s.read_artifact_bytes(tid,'spec','srs.json')
                assert s.read_artifact_bytes_sha(digest)==b'{"native":true}'
            with tenant_context(b.scope):
                assert s.manifest_snapshot(tid) is None
                assert s.manifest_revision(tid) is None
                assert s.manifest_artifacts(tid)==[]
                assert s.producer_result(job) is None
                assert s.producer_paid_evidence(job)==0
                with pytest.raises(KeyError):s.read_artifact_bytes_sha(digest)
                with pytest.raises(KeyError):s.read_artifact_bytes(tid,'spec','other.json')
