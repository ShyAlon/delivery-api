"""The deployed process route gets the original job's PG runtime, never files."""
import json,socket,sys,uuid
from types import SimpleNamespace
import pytest
from test_postgres_store import store,A
from delivery_api.tenancy import tenant_context
from delivery_api.pg_generator_process import launch_context


def test_process_launch_pins_original_job_attempt_and_private_descriptor(store,tmp_path):
    with tenant_context(A):
        tid=store.create_task('launch-'+uuid.uuid4().hex)['task_id'];sid='launch-'+uuid.uuid4().hex
        store.set_engine_session(tid,sid);job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        command=['/stale/python','-m','spec_generator.cli',str(tmp_path/'intent_model.json'),'--out',str(tmp_path/'spec')]
        with launch_context(store,cap,SimpleNamespace(job_id=job),command,{}) as (wrapped,env,fd,docs):
            assert wrapped==[sys.executable,'-m','delivery_api.pg_generator_process',*command[3:]]
            assert set(env)=={'REQUI_PG_GENERATOR_FD'} and env['REQUI_PG_GENERATOR_FD']==str(fd)
            duplicate=socket.fromfd(fd,socket.AF_UNIX,socket.SOCK_STREAM)
            with duplicate,duplicate.makefile('rb') as stream:cfg=json.loads(stream.readline())
            assert cfg=={'version':1,'dsn':store._postgres.dsn,'owner_id':A.owner_id,'auth_session_id':A.session_id,'task_id':tid,'session_id':sid,'generation':1,'job_id':job,'attempt':1}
            assert docs.saver.attempt==1 and not (tmp_path/'spec').exists()
        store.finish_worker(job)
        with pytest.raises(Exception):
            with launch_context(store,cap,SimpleNamespace(job_id=job),command,{}):pytest.fail('finished job launched')


def test_process_launch_refuses_existing_result_without_reproduction(store,tmp_path):
    from delivery_api.same_host_producer import ProducerRecoveryPaused
    with tenant_context(A):
        tid=store.create_task('result-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        store.record_producer_result(job,tid,1,str(tmp_path),'0'*64)
        with pytest.raises(ProducerRecoveryPaused):
            with launch_context(store,cap,SimpleNamespace(job_id=job),[sys.executable,'-m','spec_generator.cli'],{}):pytest.fail('producer repeated')


@pytest.mark.parametrize("revocation", ["stop", "retry", "finish", "other_owner"])
def test_original_process_documents_reject_revocation(store,tmp_path,revocation):
    from delivery_api.tenancy import TenantAccessDenied
    from test_postgres_store import B
    with tenant_context(A):
        tid=store.create_task('revoke-'+uuid.uuid4().hex)['task_id']
        store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        with launch_context(store,cap,SimpleNamespace(job_id=job),[sys.executable,'-m','spec_generator.cli'],{}) as (_,_,_,docs):
            docs.write('spec/needs.json',b'original')
            if revocation=='stop':store.request_task_stop(tid)
            elif revocation=='retry':
                store.pause_interrupted_worker(job);assert store.claim_worker(job)
            elif revocation=='finish':store.finish_worker(job)
            scope=B if revocation=='other_owner' else A
            with tenant_context(scope),pytest.raises(TenantAccessDenied):
                docs.write('spec/needs.json',b'late')
        assert not list(tmp_path.iterdir())

from test_attempt_ledger import ledger,Q


def test_process_launch_refuses_unsettled_paid_evidence_without_reproduction(ledger):
    from delivery_api.same_host_producer import ProducerRecoveryPaused
    store,tid,job=ledger
    with tenant_context(A):
        cap=__import__('delivery_api.worker_capability',fromlist=['WorkerCapability']).WorkerCapability.capture(store,tid)
        store.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'test-process-dispatch')
        assert store.producer_result(job) is None and store.producer_paid_evidence(job)>0
        with pytest.raises(ProducerRecoveryPaused,match='provider evidence exists'):
            with launch_context(store,cap,SimpleNamespace(job_id=job),[sys.executable,'-m','spec_generator.cli'],{}):
                pytest.fail('uncertain paid work repeated')


@pytest.mark.parametrize('command', [[],['python','-c','pass'],['python','-m','untrusted.module']])
def test_process_adapter_only_wraps_fixed_generator_module(command):
    with pytest.raises(ValueError,match='fixed generator module'):
        with launch_context(None,None,None,command,{}):pytest.fail('arbitrary module accepted')


def test_process_publication_failure_rolls_back_manifest_result_and_phase(store,tmp_path,monkeypatch):
    from delivery_api.runner import ProcessRunner
    with tenant_context(A):
        tid=store.create_task('atomic-process-'+uuid.uuid4().hex)['task_id']
        store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        with launch_context(store,cap,SimpleNamespace(job_id=job),[sys.executable,'-m','spec_generator.cli'],{}) as (_,_,_,docs):
            from pathlib import Path
            raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes()
            from vmodel_contracts.needs import StakeholderNeedsSet
            StakeholderNeedsSet.model_validate_json(raw)
            docs.write('spec/needs.json',raw)
            store.update_phase(tid,'needs','running')
            runner=ProcessRunner(store)
            mark=store.mark_producer_published
            monkeypatch.setattr(store,'mark_producer_published',lambda *a:(_ for _ in ()).throw(RuntimeError('crash before publication receipt')))
            with pytest.raises(RuntimeError,match='publication receipt'):
                runner._commit_pg_generator_result(tid,'needs',tmp_path,docs)
            assert next(p for p in store.task(tid)['phases'] if p['phase_key']=='needs')['state']=='running'
            assert store.manifest_snapshot(tid) is None and store.producer_result(job) is None
            monkeypatch.setattr(store,'mark_producer_published',mark)
            runner._commit_pg_generator_result(tid,'needs',tmp_path,docs)
            assert next(p for p in store.task(tid)['phases'] if p['phase_key']=='needs')['state']=='awaiting_approval'
            assert store.producer_result(job)['published_at'] is not None
            assert store.read_artifact_bytes(tid,'needs','needs.json')==raw


def test_process_publication_only_recovery_uses_exact_pg_bytes_without_child(store,tmp_path,monkeypatch):
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('recover-process-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path/'lost-host'),digest)
        store.pause_interrupted_worker(job);store.claim_worker(job)
        runner=ProcessRunner(store)
        assert runner._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))
        assert store.read_artifact_bytes(tid,'needs','needs.json')==raw
        assert store.producer_result(job)['published_at'] is not None
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change',['bytes','stop','wrong_phase','wrong_owner','missing_manifest'])
def test_process_recovery_refuses_changed_authority_or_evidence(store,tmp_path,change):
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from delivery_api.tenancy import TenantAccessDenied
    from delivery_api.same_host_producer import ProducerRecoveryPaused
    from test_postgres_store import B
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('recover-refuse-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path),digest)
        if change=='bytes':docs.write('spec/needs.json',raw+b' ')
        elif change=='stop':store.request_task_stop(tid)
        elif change=='missing_manifest':store.mark_producer_published(job)
        phase='srs' if change=='wrong_phase' else 'needs'
        with tenant_context(B if change=='wrong_owner' else A),pytest.raises((TenantAccessDenied,ProducerRecoveryPaused,PermissionError)):
            ProcessRunner(store)._recover_pg_generator_result(tid,phase,cap,SimpleNamespace(job_id=job))
        assert store.manifest_snapshot(tid) is None


def test_process_recovery_publication_failure_preserves_produced_hash_for_retry(store,tmp_path,monkeypatch):
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('recover-retry-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path),digest)
        pending=store.producer_result(job);store.update_phase(tid,'needs','running')
        runner=ProcessRunner(store);mark=store.mark_producer_published
        monkeypatch.setattr(store,'mark_producer_published',lambda *a:(_ for _ in ()).throw(RuntimeError('receipt failure')))
        with pytest.raises(RuntimeError,match='receipt failure'):
            runner._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))
        assert store.producer_result(job)==pending and store.manifest_snapshot(tid) is None
        assert next(p for p in store.task(tid)['phases'] if p['phase_key']=='needs')['state']=='running'
        monkeypatch.setattr(store,'mark_producer_published',mark)
        assert runner._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))
        assert runner._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))
        assert store.read_artifact_bytes(tid,'needs','needs.json')==raw


def test_published_process_receipt_cannot_mask_a_different_manifest(store,tmp_path):
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from delivery_api.same_host_producer import ProducerRecoveryPaused
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('receipt-manifest-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path),digest)
        store.publish_task_revision_bytes(tid,[{'phase_key':'needs','name':'needs.json','data':raw+b' '}],writer_instance='test-only')
        store.mark_producer_published(job)
        with pytest.raises(ProducerRecoveryPaused,match='differs from the ready manifest'):
            ProcessRunner(store)._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))


def test_process_run_recovery_never_spawns_or_changes_provider_ledger(ledger,tmp_path,monkeypatch):
    import asyncio
    from pathlib import Path
    from delivery_api import config
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from delivery_api.worker_capability import WorkerCapability
    from delivery_api.producer_admission import WorkerAdmissionGateway,worker_gateway
    from delivery_api.tenancy import session_root
    store,tid,job=ledger
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    with tenant_context(A):
        cap=WorkerCapability.capture(store,tid);docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        store.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'already-paid-or-held')
        with store._postgres.transaction() as c:
            before=c.execute('SELECT * FROM provider_attempts WHERE job_id=%s',(job,)).fetchall()
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path/'dead-host'),digest)
        runner=ProcessRunner(store)
        async def no_child(*a,**k):raise AssertionError('recovery must not spawn or produce')
        monkeypatch.setattr(runner,'_run',no_child)
        monkeypatch.setattr(asyncio,'create_subprocess_exec',no_child)
        root=session_root(tmp_path,A,cap.session_id)
        gateway=WorkerAdmissionGateway(store,job,require_prepaid=False,resolve_quote=lambda **kw:(_ for _ in ()).throw(AssertionError('no quote during recovery')),measure_usage=lambda r:(_ for _ in ()).throw(AssertionError('no provider usage during recovery')))
        with worker_gateway(gateway):
            assert asyncio.run(runner.run(tid,[sys.executable,'-m','spec_generator.cli','--out',str(root/'spec')],cwd=tmp_path,phase='needs',env={'VMODEL_ARTIFACTS_DIR':str(root.parent)}))==0
        with store._postgres.transaction() as c:
            assert c.execute('SELECT * FROM provider_attempts WHERE job_id=%s',(job,)).fetchall()==before
        assert store.read_artifact_bytes(tid,'needs','needs.json')==raw
        assert not root.exists()
        # Match production job completion, then test-only fixture may delete.
        store.finish_worker(job)
        store.request_task_stop(tid)
        import psycopg
        from test_postgres_store import DSN
        with psycopg.connect(DSN) as c:
            c.execute('DELETE FROM provider_attempts WHERE job_id=%s',(job,))
        store.confirm_task_stopped(tid,engine_quiescent=True)


def test_process_documents_and_recovery_refuse_revoked_auth_session(store,tmp_path):
    import psycopg
    from test_postgres_store import DSN
    from delivery_api.runner import ProcessRunner
    from delivery_api.generator_documents import GeneratorDocuments,document_set_hash
    from delivery_api.tenancy import TenantAccessDenied
    from pathlib import Path
    with tenant_context(A):
        tid=store.create_task('revoked-process-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        raw=(Path(__file__).parent/'fixtures/coding-package/spec/needs.json').read_bytes();docs.write('spec/needs.json',raw)
        digest,_=document_set_hash(docs);store.record_producer_result(job,tid,cap.generation,str(tmp_path),digest)
        with psycopg.connect(DSN) as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
        try:
            with pytest.raises(TenantAccessDenied):docs.write('spec/needs.json',raw+b' ')
            with pytest.raises(TenantAccessDenied):ProcessRunner(store)._recover_pg_generator_result(tid,'needs',cap,SimpleNamespace(job_id=job))
        finally:
            with psycopg.connect(DSN) as admin:admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(A.session_id,A.owner_id))
        assert store.manifest_snapshot(tid) is None


@pytest.mark.parametrize('field',['task_id','session_id','generation','attempt','job_id','owner'])
def test_child_runtime_rejects_launch_binding_substitution(store,field):
    from delivery_api.pg_generator_process import capture_binding
    from delivery_api.tenancy import TenantAccessDenied
    from test_postgres_store import B
    with tenant_context(A):
        tid=store.create_task('binding-process-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'launch-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');cap=store.claim_worker(job)
        other=store.create_task('binding-other-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(other,'launch-'+uuid.uuid4().hex)
        other_job=store.enqueue_worker(other,'phase:needs');store.claim_worker(other_job)
        cfg={'task_id':tid,'session_id':cap.session_id,'generation':cap.generation,'attempt':1,'job_id':job}
        substitutions={'task_id':other,'session_id':'other-session','generation':2,'attempt':2,'job_id':other_job}
        if field!='owner':cfg[field]=substitutions[field]
        with tenant_context(B if field=='owner' else A),pytest.raises((TenantAccessDenied,PermissionError)):
            capture_binding(store,cfg)
