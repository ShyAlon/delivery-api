"""Job-fenced durable generator documents, no session filesystem authority."""
import uuid
import pytest
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context,TenantAccessDenied


def test_documents_exact_bytes_overwrite_clear_and_owner_fence(store):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('documents-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        docs=GeneratorDocuments(store,cap,job)
        assert docs.read('spec/srs_fills.partial.json') is None
        docs.write('spec/srs_fills.partial.json',b'{"a":1}\n')
        assert docs.read('spec/srs_fills.partial.json')==b'{"a":1}\n'
        docs.write('spec/srs_fills.partial.json',b'{"a":2}')
        assert docs.read('spec/srs_fills.partial.json')==b'{"a":2}'
        docs.clear('spec/srs_fills.partial.json');assert docs.read('spec/srs_fills.partial.json') is None
        for path in ('../foreign','/tmp/file','spec/../x','spec//x','spec/.secret','spec/a b'):
            with pytest.raises(ValueError):docs.write(path,b'{}')
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):docs.read('spec/srs.json')
        store.finish_worker(job)
        with pytest.raises(TenantAccessDenied):docs.write('spec/srs.json',b'{}')


def test_documents_preserve_original_attempt_and_reject_tampered_bytes(store):
    import psycopg
    from test_postgres_store import DSN
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('documents-fence-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/architecture.partial.json',b'{"recorded":true}')
        with psycopg.connect(DSN) as admin:
            admin.execute('UPDATE generator_documents SET payload=%s WHERE execution_session_id=%s AND document_key=%s',(b'{}',cap.session_id,'spec/architecture.partial.json'))
        with pytest.raises(ValueError,match='hash'):docs.read('spec/architecture.partial.json')
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE worker_jobs SET attempt=attempt+1 WHERE job_id=%s',(job,))
        with pytest.raises(TenantAccessDenied):docs.clear('spec/architecture.partial.json')


def test_document_table_is_hidden_from_browser_and_scoped_at_db_layer(store):
    import psycopg
    from test_postgres_store import DSN
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('documents-rls-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        GeneratorDocuments(store,cap,job).write('spec/srs.partial.json',b'private')
        with store._postgres.transaction() as conn:
            with pytest.raises(Exception):
                with conn.transaction():conn.execute('SET LOCAL ROLE authenticated');conn.execute('SELECT * FROM generator_documents')
    with tenant_context(B):
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT 1 FROM generator_documents WHERE execution_session_id=%s',(cap.session_id,)).fetchone() is None
            with pytest.raises(Exception):
                with conn.transaction():conn.execute('UPDATE generator_documents SET payload=%s,sha256=%s WHERE execution_session_id=%s',(b'foreign','f'*64,cap.session_id));conn.execute('INSERT INTO generator_documents VALUES (%s,%s,%s,1,%s,%s,%s,0)',(cap.session_id,B.owner_id,B.session_id,'spec/foreign.json',b'{}','f'*64))


def test_document_append_preserves_history_hash_and_tombstone_reset(store):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('documents-append-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.append('spec/artifact-lifecycle.jsonl',b'first\n');docs.append('spec/artifact-lifecycle.jsonl',b'second\n')
        assert docs.read('spec/artifact-lifecycle.jsonl')==b'first\nsecond\n'
        docs.clear('spec/artifact-lifecycle.jsonl');docs.append('spec/artifact-lifecycle.jsonl',b'new\n')
        assert docs.read('spec/artifact-lifecycle.jsonl')==b'new\n'


def test_native_pg_document_runtime_preserves_partials_lifecycle_and_views(store,tmp_path,monkeypatch):
    import json
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from spec_generator.cli import _make_partials,_atomic_write,_make_persist
    from spec_generator.artifact_lifecycle import ArchitectureLifecycle
    from spec_generator import lineage
    from spec_generator.docs import archive_review
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('documents-runtime-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);root=tmp_path/sid
        runtime=JobRuntime({'needs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,documents=GeneratorDocuments(store,cap,job))
        runtime.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,session_root=root)
        from pathlib import Path
        monkeypatch.setattr(Path,'read_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('filesystem read forbidden')))
        monkeypatch.setattr(Path,'write_text',lambda *a,**k:(_ for _ in ()).throw(AssertionError('filesystem write forbidden')))
        with job_runtime(runtime):
            partials=_make_partials(root/'spec');partials.save('srs_fills',{'done':['R-1']})
            assert partials.load('srs_fills')=={'done':['R-1']}
            _atomic_write(root/'spec/needs.json','{"needs":[]}')
            life=ArchitectureLifecycle(root/'spec',sid,{'id':'baseline'});life.begin_or_checkpoint({});life.commit_finalize(life.finalize({'done':1}))
            assert life.state.value=='complete'
            sha=lineage.sha256_file(root/'spec/needs.json')
            lineage.bind(root/'spec',task_id=tid,session_id=sid,generation=cap.generation,phase='needs',name='needs.json',sha256=sha,source='stage-commit')
            record=lineage.load(root/'spec');lineage.verify_file(record,phase='needs',name='needs.json',path=root/'spec/needs.json')
            archive_review(root/'spec',{'round':1,'findings':[]},intent={'session_id':sid})
            assert b'Stakeholder needs' in runtime.documents.read('docs/needs.md')
            persist=_make_persist({'session_id':sid},root/'spec');persist.content_evidence({'rejected':1});persist.content_evidence({'rejected':2})
            assert len(json.loads(runtime.documents.read('spec/content-rejections.json')))==2
            partials.clear('srs_fills');assert partials.load('srs_fills') is None
        assert not root.exists()


def test_document_capture_is_exact_declared_set_and_rejects_tamper(store):
    from delivery_api.generator_documents import GeneratorDocuments
    import psycopg
    from test_postgres_store import DSN
    with tenant_context(A):
        tid=store.create_task('documents-capture-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/srs.json',b'{}');docs.write('spec/review-history.json',b'private')
        assert docs.capture(['spec/srs.json','spec/missing.json'])=={'spec/srs.json':b'{}'}
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE generator_documents SET payload=%s WHERE execution_session_id=%s AND document_key=%s',(b'changed',cap.session_id,'spec/srs.json'))
        with pytest.raises(ValueError,match='hash'):docs.capture(['spec/srs.json'])


def test_publish_captured_document_bytes_validates_and_preserves_manifest(store,tmp_path):
    from delivery_api.generator_documents import GeneratorDocuments
    from delivery_api.artifact_publication import publish_document_revision
    with tenant_context(A):
        tid=store.create_task('documents-publish-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        store.publish_task_revision_bytes(tid,[{'phase_key':'baseline','name':'system-baseline.json','data':b'{"baseline":1}'}],writer_instance='test-only')
        docs.write('spec/srs.json',b'{"invalid":true}')
        rows=publish_document_revision(store,tid,docs,writer_instance='test-only')
        snapshot=store.manifest_snapshot(tid,cap.generation)
        members={(r['phase_key'],r['name']):r for r in snapshot['rows']}
        assert ('baseline','system-baseline.json') in members
        assert store.read_artifact_bytes_sha(members[('srs','srs.json')]['sha256'])==b'{"invalid":true}'
        assert members[('srs','srs.json')]['valid'] is False


def test_document_publication_failure_is_atomic_and_attempt_fenced(store,monkeypatch):
    from delivery_api.generator_documents import GeneratorDocuments
    from delivery_api import artifact_publication as publication
    import psycopg
    from test_postgres_store import DSN
    with tenant_context(A):
        tid=store.create_task('documents-publish-fault-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        store.publish_task_revision_bytes(tid,[{'phase_key':'baseline','name':'system-baseline.json','data':b'{"baseline":1}'}],writer_instance='test-only')
        before=store.manifest_snapshot(tid,cap.generation);docs.write('spec/srs.json',b'{}')
        original=store._publish_artifact_bytes
        def fail(*a,**k):
            result=original(*a,**k)
            if a[1]=='srs':raise ValueError('injected post-insert publication failure')
            return result
        monkeypatch.setattr(store,'_publish_artifact_bytes',fail)
        with pytest.raises(ValueError,match='injected'):publication.publish_document_revision(store,tid,docs)
        assert store.manifest_snapshot(tid,cap.generation)==before
        assert docs.read('spec/srs.json')==b'{}'
        monkeypatch.setattr(store,'_publish_artifact_bytes',original)
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE worker_jobs SET attempt=attempt+1 WHERE job_id=%s',(job,))
        with pytest.raises(TenantAccessDenied):publication.publish_document_revision(store,tid,docs)
        assert store.manifest_snapshot(tid,cap.generation)==before


def test_prepare_document_workspace_seeds_manifest_preserves_partial_and_refuses_settled_drift(store):
    from delivery_api.generator_documents import GeneratorDocuments,prepare_document_workspace,document_set_hash,pin_document_artifacts,document_substitutions
    with tenant_context(A):
        tid=store.create_task('documents-seed-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':b'{"intent":1}'},
            {'phase_key':'baseline','name':'system-baseline.json','data':b'{"baseline":1}'},
            {'phase_key':'needs','name':'needs.json','data':b'{"needs":[]}'}],writer_instance='test-only')
        docs.write('spec/srs_fills.partial.json',b'{"completed":[1]}')
        prepare_document_workspace(store,tid,docs,current_phase='srs')
        assert docs.read('interview/intent_model.json')==b'{"intent":1}'
        assert docs.read('spec/system-baseline.json')==b'{"baseline":1}'
        assert docs.read('spec/srs_fills.partial.json')==b'{"completed":[1]}'
        digest,count=document_set_hash(docs);assert count==2
        task={'phases':[{'phase_key':'needs','state':'approved'}]}
        pinned=pin_document_artifacts(task,docs,current_phase='srs')
        docs.write('spec/srs.json',b'{"new":true}');assert document_substitutions(docs,*pinned)==[]
        docs.write('spec/needs.json',b'{"different":true}')
        assert document_substitutions(docs,*pinned)==[('changed','needs','needs.json')]
        with pytest.raises(ValueError,match='settled'):prepare_document_workspace(store,tid,docs,current_phase='srs')
        assert docs.read('spec/needs.json')==b'{"different":true}'


def test_child_documents_require_original_attempt_and_claim_epoch(store):
    from types import SimpleNamespace
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.generator_documents import ChildGeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('child-docs-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        binding=SimpleNamespace(session_id=sid,owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,auth_session_id=A.session_id)
        claim=pg.claim(sid,A.owner_id,'child-docs-test',60)
        docs=ChildGeneratorDocuments(pg,claim,binding)
        docs.write('spec/srs.json',b'{"child":1}');docs.append('spec/history.jsonl',b'first\n')
        assert docs.read('spec/srs.json')==b'{"child":1}'
        assert docs.capture(['spec/history.jsonl'])=={'spec/history.jsonl':b'first\n'}
        assert pg.release(claim)
        with pytest.raises(TenantAccessDenied):docs.write('spec/srs.json',b'{}')
        claim2=pg.claim(sid,A.owner_id,'child-docs-test-new',60)
        fresh=ChildGeneratorDocuments(pg,claim2,binding)
        assert fresh.read('spec/srs.json')==b'{"child":1}'
        with pytest.raises(TenantAccessDenied):docs.read('spec/srs.json')
        store.finish_worker(job)
        with pytest.raises(TenantAccessDenied):fresh.clear('spec/srs.json')
        pg.release(claim2)


def test_child_proposal_retention_is_immutable_and_claim_fenced(store):
    from types import SimpleNamespace
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.generator_documents import ChildGeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('child-proposal-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        binding=SimpleNamespace(session_id=sid,owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,auth_session_id=A.session_id)
        claim=pg.claim(sid,A.owner_id,'child-proposal-test',60);docs=ChildGeneratorDocuments(pg,claim,binding)
        proposal={'proposal_id':uuid.uuid4().hex,'phase':'srs','evidence':{'exact':1}}
        docs.retain_proposal(proposal);docs.retain_proposal(proposal)
        assert store.decision_review(tid,'pg-revision-proposal:'+proposal['proposal_id'],'result')==proposal
        with pytest.raises(ValueError,match='conflicting'):docs.retain_proposal({**proposal,'phase':'architecture'})
        pg.release(claim)
        with pytest.raises(TenantAccessDenied):docs.retain_proposal({**proposal,'proposal_id':uuid.uuid4().hex})


def test_child_pg_publication_fence_uses_no_marker_and_stop_blocks_docs(store,tmp_path):
    from types import SimpleNamespace
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.generator_documents import ChildGeneratorDocuments
    from delivery_api.pg_execution_fence import ChildPostgresExecutionFence
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.stop_fence import guarded_publish,StopRequested
    with tenant_context(A):
        tid=store.create_task('child-pg-fence-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        binding=SimpleNamespace(session_id=sid,owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,auth_session_id=A.session_id)
        claim=pg.claim(sid,A.owner_id,'child-pg-fence',60);docs=ChildGeneratorDocuments(pg,claim,binding)
        fence=ChildPostgresExecutionFence(docs,root=tmp_path/sid)
        with cli_execution(fence,None):
            guarded_publish(lambda:docs.write('spec/srs.json',b'original'))
            store.request_task_stop(tid)
            with pytest.raises(StopRequested):guarded_publish(lambda:docs.write('spec/srs.json',b'late'))
        assert not (tmp_path/sid).exists()
        pg.release(claim)


def test_child_pg_writer_lifetime_survives_deadline_scope_until_thread_exits(store,tmp_path):
    from types import SimpleNamespace
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.generator_documents import ChildGeneratorDocuments
    from delivery_api.pg_execution_fence import ChildPostgresExecutionFence,PostgresControlFence
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.stop_fence import track_inflight_worker
    with tenant_context(A):
        tid=store.create_task('child-writer-lifetime-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        binding=SimpleNamespace(session_id=sid,owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,auth_session_id=A.session_id)
        claim=pg.claim(sid,A.owner_id,'child-writer-lifetime',60);docs=ChildGeneratorDocuments(pg,claim,binding)
        fence=ChildPostgresExecutionFence(docs,root=tmp_path/sid);control=PostgresControlFence(store,cap)
        with cli_execution(fence,None):
            release=track_inflight_worker()
            assert control.writer_quiescent() is False
        assert control.writer_quiescent() is False
        release();release()
        assert control.writer_quiescent() is True
        assert not (tmp_path/sid).exists()
        pg.release(claim)


def test_owner_document_reader_works_after_worker_finished_and_has_no_write_authority(store):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('owner-doc-reader-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/srs.json',b'exact');store.finish_worker(job)
        reader=GeneratorDocuments.reader(store,cap)
        assert reader.read('spec/srs.json')==b'exact'
        assert reader.capture(['spec/srs.json'])=={'spec/srs.json':b'exact'}
        for change in (lambda:reader.write('spec/srs.json',b'changed'),lambda:reader.clear('spec/srs.json'),lambda:reader.append('spec/srs.json',b'late')):
            with pytest.raises(PermissionError,match='read-only'):change()
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):reader.read('spec/srs.json')


def test_orchestrator_baseline_uses_pg_inputs_and_never_creates_workspace(store,tmp_path,monkeypatch):
    from delivery_api.orchestrator import PhaseOrchestrator
    from types import SimpleNamespace
    from vmodel_contracts import CONTRACT_VERSION
    with tenant_context(A):
        tid=store.create_task('baseline-no-files-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':('{"contract_version":"'+CONTRACT_VERSION+'"}').encode()}],writer_instance='test-only')
        before=store.manifest_snapshot(tid)
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        orchestrator=PhaseOrchestrator(store,SimpleNamespace())
        path=orchestrator._ensure_greenfield(tid)
        assert path.name=='system-baseline.json' and not path.exists()
        assert orchestrator._baseline_exists(tid)
        import json
        baseline=json.loads(store.read_artifact_bytes(tid,'baseline','system-baseline.json'))
        assert baseline['mode']=='greenfield' and baseline['system_name']==store.task(tid)['title']
        assert store.read_artifact_bytes(tid,'intent','intent_model.json')==('{"contract_version":"'+CONTRACT_VERSION+'"}').encode()
        marker=store.manifest_snapshot(tid)
        assert orchestrator._ensure_greenfield(tid)==path and store.manifest_snapshot(tid)==marker
        assert not path.parent.exists()


def test_pg_baseline_resolution_validates_owned_bytes_and_continues_without_files(store,tmp_path,monkeypatch):
    from delivery_api.orchestrator import PhaseOrchestrator
    from types import SimpleNamespace
    from vmodel_contracts import CONTRACT_VERSION
    import asyncio
    with tenant_context(A):
        tid=store.create_task('baseline-resolve-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':('{"contract_version":"'+CONTRACT_VERSION+'"}').encode()}],writer_instance='test-only')
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path));runner=SimpleNamespace(active_work={tid:'waiting'});calls=[]
        orch=PhaseOrchestrator(store,runner)
        async def execute(task,phase):calls.append((task,phase))
        monkeypatch.setattr(orch,'execute_phase',execute)
        with pytest.raises(ValueError,match='missing'):asyncio.run(orch.resolve_baseline_requirement(tid,'validate_file'))
        assert runner.active_work=={tid:'waiting'} and calls==[]
        asyncio.run(orch.resolve_baseline_requirement(tid,'greenfield'))
        asyncio.run(orch.resolve_baseline_requirement(tid,'validate_file'))
        assert calls==[(tid,'architecture'),(tid,'architecture')] and runner.active_work=={}
        assert not (tmp_path/sid).exists()


def test_pg_artifact_view_uses_published_exact_bytes_and_pg_readable_companion(store,tmp_path,monkeypatch):
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('artifact-view-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        raw=b'{"needs":[]}\n'
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw}],writer_instance='test-only')
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job);docs.write('spec/needs.json',raw);docs.write('spec/needs.md',b'# Readable needs\n')
        store.finish_worker(job);monkeypatch.setattr(app,'store',store)
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        view=app.task_artifact_view(tid,'needs','needs.json')
        assert view['raw_content']=='{\n  "needs": []\n}' and view['content']=='# Readable needs\n' and view['format']=='markdown'
        assert view['display_name']=='needs.md' and not list(tmp_path.rglob('needs.json'))
        with tenant_context(B):
            from fastapi import HTTPException
            with pytest.raises(HTTPException):app.task_artifact_view(tid,'needs','needs.json')


def test_pg_intent_view_preserves_recorded_interview_turns_without_legacy_checkpoint(store,tmp_path,monkeypatch):
    from delivery_api import app
    with tenant_context(A):
        tid=store.create_task('intent-transcript-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':b'{"intent":"exact"}'}],writer_instance='test-only')
        store.add_event(tid,'waiting_input','Who uses it?',phase_key='intent',payload={'question':'Who uses it?'})
        store.add_event(tid,'answer','You: Operators',phase_key='intent',payload={'answer':'Operators'})
        monkeypatch.setattr(app,'store',store);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        view=app.task_artifact_view(tid,'intent','intent_model.json')
        assert 'Who uses it?' in view['content'] and '**Answer:** Operators' in view['content']
        assert view['raw_content']=='{\n  "intent": "exact"\n}' and not list(tmp_path.rglob('*.sqlite'))


def test_published_view_remains_readable_after_stop_and_never_uses_stale_markdown(store,tmp_path,monkeypatch):
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('view-stop-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':b'{"needs":[]}'}],writer_instance='test-only')
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/needs.json',b'{"old":1}');docs.write('spec/needs.md',b'# Old needs')
        store.finish_worker(job);monkeypatch.setattr(app,'store',store);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        view=app.task_artifact_view(tid,'needs','needs.json')
        assert view['format']=='json' and 'Old needs' not in view['content']
        store.request_task_stop(tid)
        view=app.task_artifact_view(tid,'needs','needs.json')
        assert view['format']=='json' and 'needs' in view['raw_content']


def test_updating_canonical_json_invalidates_readable_companion_in_same_transaction(store):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('markdown-invalidation-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/needs.json',b'first');docs.write('spec/needs.md',b'old render')
        docs.write('spec/needs.json',b'second')
        assert docs.read('spec/needs.md') is None and docs.read('spec/needs.json')==b'second'


def test_pg_live_phase_output_shows_exact_partial_without_files(store,tmp_path,monkeypatch):
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('phase-output-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        store.update_phase(tid,'srs','running');docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/srs_fills.partial.json',b'{"R-1":{"title":"Offline evidence"}}')
        monkeypatch.setattr(app,'store',store);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path));monkeypatch.delenv('DELIVERY_LOCAL_PREVIEW',raising=False)
        output=app.task_phase_output(tid)
        assert output['status']=='in_progress' and output['phase_key']=='srs' and output['generation']==1
        assert output['items']==[{'id':'R-1','title':'Offline evidence'}]
        assert not list(tmp_path.rglob('srs_fills.partial.json'))
        docs.clear('spec/srs_fills.partial.json')
        assert app.task_phase_output(tid)['status']=='waiting'
        store.update_phase(tid,'srs','failed',detail='test cleanup')


@pytest.mark.parametrize('phase,key,payload,expected',[('architecture','architecture.partial.json',{'component_bodies':{'api':{'name':'api'}}},[{'id':'api','name':'api'}]),('specs','items.partial.json',[{'spec_id':'SPEC-1'}],[{'spec_id':'SPEC-1'}]),('validation','validation_fills.partial.json',{'N-1':{'items':[{'validation_id':'V-1'}]}},[{'validation_id':'V-1'}]),('tests','tests_fills.partial.json',{'T-1':{'description':'Check offline'}},[{'id':'T-1','description':'Check offline'}])])
def test_pg_phase_partial_formats_and_stop_never_promotes_old_generation(store,monkeypatch,phase,key,payload,expected):
    import json
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('panel-format-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:'+phase);cap=store.claim_worker(job);store.update_phase(tid,phase,'running')
        GeneratorDocuments(store,cap,job).write('spec/'+key,json.dumps(payload).encode())
        monkeypatch.setattr(app,'store',store)
        shown=app.task_phase_output(tid);assert shown['items']==expected and shown['status']=='in_progress'
        store.request_task_stop(tid)
        shown=app.task_phase_output(tid);assert shown['status']=='waiting' and shown['items']==[] and shown['generation']==2


def test_pg_phase_panel_final_fallback_and_invalid_states_are_manifest_owned(store,monkeypatch):
    import hashlib
    from delivery_api import app
    with tenant_context(A):
        tid=store.create_task('panel-final-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        monkeypatch.setattr(app,'store',store);store.update_phase(tid,'needs','approved');store.update_phase(tid,'srs','running')
        raw=b'{"needs":[{"need_id":"N-1","statement":"Evidence"}]}'
        report={'sha256':hashlib.sha256(raw).hexdigest(),'validator':'test-only','validator_version':'test-only','valid':True,'error':None}
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw,'validation_report':report}],writer_instance='test-only')
        shown=app.task_phase_output(tid);assert shown['status']=='previous_phase' and shown['display_phase']=='needs' and shown['items'][0]['need_id']=='N-1'
        current=b'{"invalid":true}';bad={**report,'sha256':hashlib.sha256(current).hexdigest(),'valid':False,'error':'invalid'}
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw,'validation_report':report},{'phase_key':'srs','name':'srs.json','data':current,'validation_report':bad}],writer_instance='test-only')
        assert app.task_phase_output(tid)['status']=='unavailable'
        store.update_phase(tid,'srs','failed',detail='test cleanup')


def test_pg_phase_panel_refuses_partial_older_than_same_generation_restart(store,monkeypatch):
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    import psycopg,time
    from test_postgres_store import DSN
    with tenant_context(A):
        tid=store.create_task('panel-restart-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);store.update_phase(tid,'srs','running')
        docs=GeneratorDocuments(store,cap,job);docs.write('spec/srs_fills.partial.json',b'{"R-1":{"title":"Old attempt"}}')
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE phases SET started_at=%s WHERE task_id=%s AND phase_key=%s',(time.time()+1,tid,'srs'))
        monkeypatch.setattr(app,'store',store)
        shown=app.task_phase_output(tid);assert shown['status']=='waiting' and shown['items']==[]
        store.update_phase(tid,'srs','failed',detail='test cleanup')


def test_gate_document_commit_is_atomic_with_manifest_and_requires_waiting_gate(store,monkeypatch):
    from delivery_api.generator_documents import commit_gate_documents,GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('gate-documents-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        store.update_phase(tid,'needs','awaiting_approval');store.wait_at_gate(tid,'needs')
        raw=b'{"needs":[]}'
        commit_gate_documents(store,tid,'needs',{'needs.json':raw})
        assert store.read_artifact_bytes(tid,'needs','needs.json')==raw
        reader=GeneratorDocuments.owner_reader(store,tid);assert reader.read('spec/needs.json')==raw
        before=store.manifest_snapshot(tid)
        real=store.publish_task_revision_bytes
        monkeypatch.setattr(store,'publish_task_revision_bytes',lambda *a,**k:(_ for _ in ()).throw(ValueError('injected')))
        with pytest.raises(ValueError,match='injected'):commit_gate_documents(store,tid,'needs',{'needs.json':b'{"changed":true}'})
        assert reader.read('spec/needs.json')==raw and store.manifest_snapshot(tid)==before
        monkeypatch.setattr(store,'publish_task_revision_bytes',real)
        store.update_phase(tid,'needs','failed',detail='test boundary')
        with pytest.raises(ValueError,match='waiting'):commit_gate_documents(store,tid,'needs',{'needs.json':b'late'})


def test_pg_stack_decision_endpoint_updates_canonical_companion_and_lineage_without_files(store,monkeypatch,tmp_path):
    import json
    from types import SimpleNamespace
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('stack-endpoint-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        arch={'stack_entry_dispositions':[{'key':'database','status':'research_required','research_brief':'Which database?'}]}
        companion={'revision':'a'*64,'slots':[{'key':'database','brief':'Which database?','options':['A','B'],'founder_decision':None,'prefill':None}]}
        store.publish_task_revision_bytes(tid,[{'phase_key':'architecture','name':'architecture.json','data':json.dumps(arch).encode()},
            {'phase_key':'architecture','name':'architecture.decisions.json','data':json.dumps(companion).encode()}],writer_instance='test-only')
        store.update_phase(tid,'architecture','awaiting_approval');store.wait_at_gate(tid,'architecture')
        monkeypatch.setattr(app,'store',store);monkeypatch.setattr(app,'_gate_review',lambda *a:{'semantic_review':None,'stack_decisions':app._read_stack_decisions(tid),'task':store.task(tid)})
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        body=app.StackDecisions(revision='a'*64,decisions=[{'key':'database','text':'Postgres','reason':'One durable store'}])
        result=app.decide_stack_gate(tid,'gate-architecture',body,SimpleNamespace(label='founder'))
        assert result['status']=='saved' and result['open']==[]
        canonical=json.loads(store.read_artifact_bytes(tid,'architecture','architecture.json'))
        decision=canonical['stack_entry_dispositions'][0]['founder_decision']
        assert decision['text']=='Postgres' and decision['actor']=='founder' and decision['reason']=='One durable store'
        reader=GeneratorDocuments.owner_reader(store,tid)
        assert json.loads(reader.read('spec/architecture.decisions.json'))['slots'][0]['founder_decision']==decision
        assert not list(tmp_path.rglob('architecture.json'))
        store.update_phase(tid,'architecture','failed',detail='test cleanup')


def test_pg_gate_lineage_settle_and_redo_preserve_prior_bindings(store):
    import json,hashlib
    from delivery_api.generator_documents import GeneratorDocuments,transition_gate_lineage
    with tenant_context(A):
        tid=store.create_task('lineage-transition-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        record={'version':1,'entries':[{'phase':'needs','name':'needs.json','settled':True},{'phase':'srs','name':'srs.json','settled':False},{'phase':'architecture','name':'architecture.json','settled':False}]}
        docs.write('spec/lineage.json',json.dumps(record).encode());docs.write('spec/srs_fills.partial.json',b'old partial');docs.write('spec/srs.json',b'old artifact')
        store.finish_worker(job)
        transition_gate_lineage(store,tid,'srs',settled=True)
        reader=GeneratorDocuments.owner_reader(store,tid)
        assert json.loads(reader.read('spec/lineage.json'))['entries'][1]['settled'] is True
        transition_gate_lineage(store,tid,'srs',settled=False)
        assert json.loads(reader.read('spec/lineage.json'))['entries']==record['entries'][:1]
        assert reader.read('spec/srs_fills.partial.json') is None and reader.read('spec/srs.json') is None


def test_pg_gate_decision_and_lineage_roll_back_together_before_dispatch(store,monkeypatch):
    import json
    from types import SimpleNamespace
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('gate-rollback-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job)
        docs=GeneratorDocuments(store,cap,job);docs.write('spec/lineage.json',json.dumps({'entries':[{'phase':'srs','name':'srs.json','settled':False}]}).encode());store.finish_worker(job)
        store.wait_at_gate(tid,'srs');store.set_model_default(tid,'deepseek:deepseek-flash')
        monkeypatch.setattr(app,'store',store);monkeypatch.setattr(app,'cloud_execution_admitted',lambda:True)
        monkeypatch.setattr(app,'_recorded_engine_session',lambda *a:None)
        monkeypatch.setattr(app,'_task_artifact_snapshot',lambda *a:[{'phase_key':'srs','present':True,'stale':False,'valid':True}])
        before=store.task(tid);original=app._lineage_gate_settled
        def fail_after_lineage(*args):original(*args);raise ValueError('injected settle failure')
        monkeypatch.setattr(app,'_lineage_gate_settled',fail_after_lineage)
        from fastapi import HTTPException
        with pytest.raises(HTTPException,match='400'):
            app._decide_gate_sync(tid,'gate-srs',app.GateDecision(action='approve',selected_option='Proceed'),SimpleNamespace(label='founder'))
        current=store.task(tid)
        assert current['gates']==before['gates'] and current['phases']==before['phases']
        assert json.loads(GeneratorDocuments.owner_reader(store,tid).read('spec/lineage.json'))['entries'][0]['settled'] is False
        assert not any(e['kind']=='gate.approve' for e in store.events(tid))


def test_pg_phase_panel_ignores_invalid_final_from_earlier_attempt(store,monkeypatch):
    import hashlib,time,psycopg
    from test_postgres_store import DSN
    from delivery_api import app
    with tenant_context(A):
        tid=store.create_task('panel-old-invalid-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        monkeypatch.setattr(app,'store',store);store.update_phase(tid,'needs','approved');store.update_phase(tid,'srs','running')
        raw=b'{"needs":[{"need_id":"N-1","statement":"Prior evidence"}]}';bad=b'{}'
        def report(data,valid):return {'sha256':hashlib.sha256(data).hexdigest(),'validator':'test-only','validator_version':'test-only','valid':valid,'error':None if valid else 'invalid'}
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw,'validation_report':report(raw,True)},{'phase_key':'srs','name':'srs.json','data':bad,'validation_report':report(bad,False)}],writer_instance='test-only')
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE phases SET started_at=%s WHERE task_id=%s AND phase_key=%s',(time.time()+1,tid,'srs'))
        shown=app.task_phase_output(tid)
        assert shown['status']=='previous_phase' and shown['items'][0]['need_id']=='N-1'
        store.update_phase(tid,'srs','failed',detail='test cleanup')


def test_pg_artifact_snapshot_download_zip_ignore_flags_and_local_files(store,monkeypatch,tmp_path):
    import io,zipfile
    from delivery_api import app
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('db-download-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        monkeypatch.setattr(app,'store',store);monkeypatch.setattr(app.config,'CLOUD_TENANTS',False);monkeypatch.setattr(app.config,'ARTIFACT_MANIFEST_READS',False)
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path));monkeypatch.delenv('DELIVERY_LOCAL_PREVIEW',raising=False)
        raw=b'{"needs":[]}\n'
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw}],writer_instance='test-only')
        monkeypatch.setattr(Path,'is_file',lambda *a:(_ for _ in ()).throw(AssertionError('filesystem authority forbidden')))
        rows=app._task_artifact_snapshot(tid)
        assert [(r['phase_key'],r['name']) for r in rows]==[('needs','needs.json')]
        assert app.task_artifact_download(tid,'needs','needs.json').body==raw
        zipped=app.task_artifacts_zip(tid)
        with zipfile.ZipFile(io.BytesIO(zipped.body)) as archive:assert archive.read('needs/needs.json')==raw
        assert not list(tmp_path.iterdir())


def test_pg_artifact_snapshot_without_publication_does_not_scan_or_import_files(store,monkeypatch):
    from delivery_api import app
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('db-no-manifest-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        monkeypatch.setattr(app,'store',store);monkeypatch.setattr(app.config,'CLOUD_TENANTS',True);monkeypatch.setattr(app.config,'ARTIFACT_MANIFEST_READS',False)
        monkeypatch.setattr(Path,'is_file',lambda *a:(_ for _ in ()).throw(AssertionError('filesystem import forbidden')))
        assert app._task_artifact_snapshot(tid)==[]


def test_pg_validation_panel_uses_owned_ledger_and_preserves_supplemental_status(store,monkeypatch,tmp_path):
    import json
    from delivery_api import app
    from delivery_api.generator_documents import GeneratorDocuments
    from tests.test_validation_panel import NEEDS,ITEM,LEDGER
    with tenant_context(A):
        tid=store.create_task('validation-panel-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':json.dumps(NEEDS).encode()},{'phase_key':'tests','name':'tests.json','data':b'[]'}],writer_instance='test-only')
        job=store.enqueue_worker(tid,'phase:validation');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/findings-ledger.json',json.dumps(LEDGER).encode())
        monkeypatch.setattr(app,'store',store);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        panel=app._validation_panel_for(tid,json.dumps({'items':[ITEM]}))
        assert panel['item_count']==1 and panel['groups'][1]['uncovered'] is True
        assert panel['supplemental_availability']['needs']['availability']=='available'
        docs.write('spec/findings-ledger.json',b'invalid-secret')
        panel=app._validation_panel_for(tid,json.dumps({'items':[ITEM]}))
        assert panel['item_count']==1 and panel['groups'][1]['uncovered'] is False
        assert panel['supplemental_availability']['findings_ledger']=={'availability':'unavailable','reason':'invalid_or_unreadable'}
        assert 'invalid-secret' not in str(panel)
        docs.clear('spec/findings-ledger.json')
        assert app._validation_panel_for(tid,'{}')['supplemental_availability']['findings_ledger']['availability']=='absent'
        assert not list(tmp_path.iterdir());store.finish_worker(job)


def test_pg_session_bundle_discovery_and_graph_need_no_workspace(store,monkeypatch,tmp_path):
    import json
    from delivery_api import app
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('PG bundle '+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        members=[('intent','intent_model.json',{'problem_statement':'Track progress'}),('needs','needs.json',{'needs':[{'need_id':'N-1','statement':'See work'}]}),('srs','srs.json',{'functional':[{'requirement_id':'R-1','need_ids':['N-1'],'acceptance':[]}],'non_functional':[]}),('validation','validation.json',{'items':[{'validation_id':'V-1'}]})]
        store.publish_task_revision_bytes(tid,[{'phase_key':p,'name':n,'data':json.dumps(value).encode()} for p,n,value in members],writer_instance='test-only')
        monkeypatch.setattr(app,'store',store);monkeypatch.setattr(app.config,'CLOUD_TENANTS',False);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
        monkeypatch.setattr(Path,'is_dir',lambda *a:(_ for _ in ()).throw(AssertionError('no workspace authority')))
        result=app._session_bundle(sid)
        assert result['session']['session_id']==sid and 'path' not in result['session']
        assert result['needs'][0]['need_id']=='N-1' and result['validation'][0]['validation_id']=='V-1'
        assert {'from':'N-1','to':'R-1','kind':'realizes'} in result['graph']['edges']
        assert result['phase_status']['interview']=='done'
        assert sid in [row['session_id'] for row in app.sessions()['sessions']]
        with tenant_context(B):
            with pytest.raises((FileNotFoundError,TenantAccessDenied)):app._session_bundle(sid)


def test_interview_documents_lease_stop_takeover_hash_and_evidence_no_files(store,tmp_path):
    import json,psycopg
    from test_postgres_store import DSN
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_documents import InterviewDocuments
    from interview_engine.evidence import DatabaseEvidenceLake
    from interview_engine.stop_fence import StopRequested
    with tenant_context(A):
        tid=store.create_task('engine-docs-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id));claim=pg.claim(sid,A.owner_id,'documents-test',60)
        docs=InterviewDocuments(pg,claim,tid);docs.write('interview/intent_model.json',b'{}');docs.append('evidence/evidence.jsonl',b'first\n');docs.append('evidence/evidence.jsonl',b'second\n')
        assert docs.read('evidence/evidence.jsonl')==b'first\nsecond\n'
        lake=DatabaseEvidenceLake(docs,tid);binding=lake.bind_session(sid);assert lake.bind_session(sid)==binding
        lake._append('evidence',{'raw_text':'Founder input'});assert b'Founder input' in docs.read('evidence/evidence.jsonl')
        with pytest.raises(ValueError):docs.write('../foreign',b'{}')
        with pytest.raises(PermissionError):InterviewDocuments(PgSessionStore(store._postgres._pool,claims=(B.owner_id,B.session_id)),claim,tid)
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE generator_documents SET payload=%s WHERE execution_session_id=%s AND document_key=%s',(b'bad',sid,'interview/intent_model.json'))
        with pytest.raises(ValueError,match='hash'):docs.read('interview/intent_model.json')
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE engine_sessions SET lease_epoch=lease_epoch+1 WHERE session_id=%s',(sid,))
        with pytest.raises(StopRequested):docs.write('interview/intent_model.json',b'late')
        claim=pg.claim(sid,A.owner_id,'documents-test',60);fresh=InterviewDocuments(pg,claim,tid)
        store.request_task_stop(tid)
        with pytest.raises(StopRequested):fresh.write('interview/conops.json',b'late')
        assert not list(tmp_path.iterdir())


def test_pg_interview_application_constructs_and_finishes_without_canonical_files(store,tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from pathlib import Path
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.application import InterviewApplication
    from interview_engine import application
    with tenant_context(A):
        tid=store.create_task('engine-app-docs-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id));claim=pg.claim(sid,A.owner_id,'app-documents-test',60)
        source=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())
        # Schema-valid source evidence already used by the native revision tests.
        intent=source['intent_model'] if 'intent_model' in source else source
        from vmodel_contracts.interview import IntentModel
        intent=IntentModel.model_validate({**intent,'session_id':sid}).model_dump(mode='json')
        conops={**source['conops'],'session_id':sid}
        from vmodel_contracts import ConOpsDocument
        conops=ConOpsDocument.model_validate(conops).model_dump(mode='json')
        captured={}
        def fake_graph(*a,**kwargs):captured.update(kwargs);return SimpleNamespace()
        monkeypatch.setattr(application,'build_graph',fake_graph)
        app=InterviewApplication(sid,tid,'Test',artifact_root=str(tmp_path),pg_store=pg,pg_claim=claim)
        assert captured['documents'] is app.documents and not (tmp_path/sid/'interview').exists()
        app.result={'intent_model':intent,'conops':conops};app._finish()
        assert app.finished and app.documents.read('interview/intent_model.json') is not None
        assert json.loads(app.documents.read('interview/conops.json'))['session_id']==sid
        assert app.documents.read('interview/conops.md') is not None
        assert not (tmp_path/sid/'interview/intent_model.json').exists()
        pg.release(claim)


def test_real_pg_interview_graph_advances_and_takeover_restores_published_checkpoint(store,tmp_path,monkeypatch):
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.http_service import SessionRegistry,default_program_factory
    from interview_engine import model_config
    from vmodel_contracts.interactive import StartSessionRequest
    from langchain_core.messages import AIMessage
    from interview_engine.tenant_worker import _owner,_session,_root
    from pathlib import Path
    import time,psycopg
    from test_postgres_store import DSN
    class Fake:
        def __init__(self,stage):self.stage=stage
        def invoke(self,messages):
            text='{"is_web_app":true,"archetype":"saas_crud","confidence":0.9,"reason":"interactive booking"}' if self.stage=='scope_tiebreak' else '{"question":"What does a booking look like for your customers?","covered":[],"thin":[],"skipped":[],"complete":false}'
            return AIMessage(content=text)
    monkeypatch.setattr(model_config,'build_chat_model',lambda stage,**kwargs:Fake(stage))
    monkeypatch.setattr(__import__('interview_engine.session_models',fromlist=['x']),'validate',lambda *a,**k:None)
    with tenant_context(A):
        tid=store.create_task('real-engine-docs-'+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id))
        registry=SessionRegistry(default_program_factory,allow_db_provider_dispatch=True,session_store=pg,owner_id=A.owner_id,instance_id='document-host-one',lease_ttl=60)
        tokens=[(_owner,_owner.set(A.owner_id)),(_session,_session.set(A.session_id)),(_root,_root.set(tmp_path))]
        try:
            session,_=registry.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Booking software',model_route='ollama',initial_model='phi4'))
            assert session.program.documents is not None
            row=pg.load_published(sid,A.owner_id);published=row['published_checkpoint_id']
            assert published and session.open_input is not None
            assert session.program.documents.read('evidence/schema-binding.json') is not None
            assert not (tmp_path/sid/'interview/intent_model.json').exists() and not (tmp_path/sid/'interview/checkpoints.sqlite').exists()
            registry.close()
            second=SessionRegistry(default_program_factory,allow_db_provider_dispatch=True,session_store=pg,owner_id=A.owner_id,instance_id='document-host-two',lease_ttl=60)
            try:
                restored=second.get(sid)
                assert restored.program.cfg['configurable']['checkpoint_id']==published
                assert restored.open_input==session.open_input
                assert restored.program.documents.read('evidence/schema-binding.json') is not None
            finally:second.close()
        finally:
            registry.close()
            for variable,token in reversed(tokens):variable.reset(token)


def test_owner_document_read_does_not_hold_task_lock_over_engine_snapshot(store):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('reader-lock-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:intent');cap=store.claim_worker(job)
        GeneratorDocuments(store,cap,job).write('interview/usage.jsonl',b'{}\n')
        reader=GeneratorDocuments.owner_reader(store,tid)
        handle,token=store._postgres.begin_request(A)
        try:
            assert reader.read('interview/usage.jsonl')==b'{}\n'
            with store._postgres._pool.connection() as conn,conn.transaction():
                conn.execute("SELECT set_config('role','requi_backend',true),set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)",(A.owner_id,A.session_id))
                conn.execute("SET LOCAL lock_timeout='100ms'")
                assert conn.execute('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(tid,)).fetchone() is not None
        finally:
            store._postgres.end_context(token);store._postgres.finish_request(handle,False)


@pytest.mark.parametrize("observation", ["waiting_input", "decision_review"])
def test_observation_commits_before_shared_read_request_finishes(store,observation):
    with tenant_context(A):
        tid=store.create_task('wait-read-'+uuid.uuid4().hex)['task_id']
        handle,token=store._postgres.begin_request(A)
        try:
            if observation=='waiting_input':
                store.ensure_waiting_input_event(tid,'Question',phase_key='intent',payload={'input_id':'wait-1'})
            else:
                store.retain_decision_review(tid,'wait-1','revision-1',{'question':'Question'})
            with store._postgres._pool.connection() as conn,conn.transaction():
                conn.execute("SELECT set_config('role','requi_backend',true),set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)",(A.owner_id,A.session_id))
                conn.execute("SET LOCAL lock_timeout='100ms'")
                assert conn.execute('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(tid,)).fetchone()
        finally:
            store._postgres.end_context(token);store._postgres.finish_request(handle,False)


@pytest.mark.parametrize("observation", ["waiting_input", "decision_review"])
def test_observation_inside_explicit_transaction_rolls_back_atomically(store,observation):
    with tenant_context(A):
        tid=store.create_task('explicit-observation-'+uuid.uuid4().hex)['task_id']
        with pytest.raises(RuntimeError,match='abort outer transaction'):
            with store._task_operation_guard('task.rerun'):
                if observation=='waiting_input':
                    store.ensure_waiting_input_event(tid,'Question',phase_key='intent',payload={'input_id':'wait-atomic'})
                else:
                    store.retain_decision_review(tid,'wait-atomic','revision-1',{'question':'Question'})
                raise RuntimeError('abort outer transaction')
        with store._postgres.transaction() as c:
            assert c.execute('SELECT 1 FROM decision_reviews WHERE task_id=%s',(tid,)).fetchone() is None
            assert c.execute("SELECT 1 FROM events WHERE task_id=%s AND kind='waiting_input'",(tid,)).fetchone() is None


@pytest.mark.parametrize('invalid_id',['not-a-uuid',uuid.uuid4().hex.upper(),'../other'])
def test_owned_proposals_require_canonical_ids_and_cannot_change_retained_evidence(store,invalid_id):
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('proposal-fence-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        proposal={'proposal_id':uuid.uuid4().hex,'phase':'srs','evidence':{'exact':1}}
        with pytest.raises(ValueError):docs.retain_proposal({**proposal,'proposal_id':invalid_id})
        docs.retain_proposal(proposal);docs.retain_proposal(proposal)
        with pytest.raises(ValueError,match='conflicting'):docs.retain_proposal({**proposal,'phase':'architecture'})
        assert store.decision_review(tid,'pg-revision-proposal:'+proposal['proposal_id'],'result')==proposal
        store.request_task_stop(tid)
        with pytest.raises(TenantAccessDenied):docs.retain_proposal({**proposal,'proposal_id':uuid.uuid4().hex})


def test_cli_usage_sink_persists_exact_pg_rows_across_readers_without_files(store,tmp_path):
    from types import SimpleNamespace
    from spec_generator.telemetry import jsonl_sink
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('cli-usage-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);root=tmp_path/sid
        runtime=JobRuntime({'needs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,documents=GeneratorDocuments(store,cap,job))
        runtime.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=job,generation=1,session_root=root)
        raw='{"stage":"needs","prompt_tokens":3}'
        with job_runtime(runtime):jsonl_sink(root/'spec/usage.jsonl')(SimpleNamespace(model_dump_json=lambda:raw))
        assert GeneratorDocuments.reader(store,cap).read('spec/usage.jsonl')==(raw+'\n').encode()
        assert not root.exists()


def test_generator_diagnostics_keep_exact_raw_bytes_in_pg_without_local_files(store,tmp_path):
    import io,json
    from spec_generator import model_config as mc
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.model_config import StageModel
    from spec_generator.document_runtime import read_text,plain_log
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('diag-pg-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);root=tmp_path/sid
        runtime=JobRuntime({'srs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,documents=GeneratorDocuments(store,cap,job))
        runtime.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=job,generation=1,session_root=root)
        with job_runtime(runtime):
            path=mc.capture_rejected_completion('srs','R-1',1,'  raw\n','shape')
            assert read_text(path)=='  raw\n'
            mc._log_llm({'event':'diagnostic'})
            stream=plain_log(root/'spec/run.log',io.StringIO());stream.write('exact\n');stream.flush()
        reader=GeneratorDocuments.reader(store,cap)
        assert reader.read('spec/run.log')==b'exact\n'
        assert json.loads(reader.read('calls.jsonl'))['event']=='diagnostic'
        assert not root.exists()


def test_interview_diagnostics_use_real_pg_lease_and_stop_without_outer_task_lock(store,tmp_path,monkeypatch):
    import json
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_documents import InterviewDocuments
    from interview_engine.document_diagnostics import bind
    from interview_engine.raw_output import RawOutputLog
    from interview_engine.verification import VerificationLog
    from interview_engine import stop_fence
    from delivery_api.generator_documents import GeneratorDocuments
    with tenant_context(A):
        tid=store.create_task('interview-diag-'+uuid.uuid4().hex)['task_id'];sid='docs-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        pg=PgSessionStore(store._postgres._pool,claims=(A.owner_id,A.session_id));claim=pg.claim(sid,A.owner_id,'diag-test',60)
        docs=InterviewDocuments(pg,claim,tid);root=tmp_path/sid
        monkeypatch.setattr(stop_fence,'guarded_publish',lambda call:(_ for _ in ()).throw(AssertionError('no nested publication lock')))
        with bind(docs,root):
            raw=RawOutputLog(root/'interview/debug');path=raw.write('conops','item','exact\n')
            verification=VerificationLog(root/'interview/verification.jsonl');verification.write({'passed':True})
            reader=GeneratorDocuments.owner_reader(store,tid)
            assert reader.read('interview/debug/'+path.name)==b'exact\n'
            assert json.loads(reader.read('interview/verification.jsonl'))['passed'] is True
            pg.release(claim)
            fresh=pg.claim(sid,A.owner_id,'diag-new-lease',60)
            with pytest.raises(stop_fence.StopRequested):raw.write('conops','item','old lease')
            with bind(InterviewDocuments(pg,fresh,tid),root):
                store.request_task_stop(tid)
                with pytest.raises(stop_fence.StopRequested):verification.write({'late':True})
            pg.release(fresh)
        assert not root.exists()
