import uuid,json,pytest
from test_postgres_store import store,A
from delivery_api.tenancy import tenant_context
from delivery_api.generator_documents import GeneratorDocuments

def test_usage_incremental_partial_lines_invalid_rows_and_overwrite(store,monkeypatch):
    from delivery_api.pg_usage import usage_summary
    from vmodel_contracts import cost_rollup
    with tenant_context(A):
        tid=store.create_task('usage-projection-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'usage-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        row={'stage':'srs','provider':'fake','model':'fake','prompt_tokens':2,'completion_tokens':3,'price_per_mtok_prompt_usd':1,'price_per_mtok_completion_usd':2}
        raw=json.dumps(row).encode()
        docs.append('spec/usage.jsonl',raw[:10]);first=usage_summary(store,tid);assert not first['evidence']['complete']
        docs.append('spec/usage.jsonl',raw[10:]+b'\n');result=usage_summary(store,tid)
        assert result['evidence']['complete'] and result['tokens']==[0,5]
        assert result['report']==cost_rollup([row])
        original=GeneratorDocuments._verified_bytes
        def reject_old(r):
            if bytes(r['payload'])==raw[:10]:raise AssertionError('re-read old chunk')
            return original(r)
        monkeypatch.setattr(GeneratorDocuments,'_verified_bytes',staticmethod(reject_old))
        assert usage_summary(store,tid)==result
        docs.append('spec/usage.jsonl',b'bad\n');assert not usage_summary(store,tid)['evidence']['complete']
        monkeypatch.setattr(GeneratorDocuments,'_verified_bytes',staticmethod(original))
        docs.write('spec/usage.jsonl',raw+b'\n');assert usage_summary(store,tid)['report']==cost_rollup([row])

def test_projection_is_durable_and_exact_across_appends_clear_and_invalid_rows(store):
    from delivery_api.pg_usage import usage_summary,usage_rows
    from vmodel_contracts import cost_rollup
    with tenant_context(A):
        tid=store.create_task('usage-exact-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'usage-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');docs=GeneratorDocuments(store,store.claim_worker(job),job)
        rows=[dict(stage=str(i%3),provider='fake',model=str(i%2),prompt_tokens=i,completion_tokens=i+1,cached_tokens=i//2,
            price_per_mtok_prompt_usd=0.031,price_per_mtok_completion_usd=1.7) for i in range(100)]
        for area in ('interview','spec'):
            for offset in range(0,len(rows),10):
                docs.append(area+'/usage.jsonl',b''.join(json.dumps(r).encode()+b'\n' for r in rows[offset:offset+10]))
                summary=usage_summary(store,tid);batches,evidence=usage_rows(store,tid)
                assert summary['report']==cost_rollup(batches[0]+batches[1]) and summary['evidence']==evidence
        docs.append('interview/usage.jsonl',json.dumps(rows[0]).encode()+b'\n')
        assert usage_summary(store,tid)['report']==cost_rollup(rows+[rows[0]]+rows)
        for bad in (b'\xff\n',b'[]\n',b'{"prompt_tokens":true}\n',b'{"latency_s":-1}\n',b'{"prompt_tokens":1.2}\n'):
            docs.write('interview/usage.jsonl',bad)
            assert usage_summary(store,tid)['evidence']==usage_rows(store,tid)[1]
        docs.clear('interview/usage.jsonl');assert usage_summary(store,tid)['report']==cost_rollup(rows)
        docs.clear('spec/usage.jsonl');assert usage_summary(store,tid)['report']=={}


def test_lifecycle_latest_pair_durable_resume_tamper_and_overwrite(store,tmp_path,monkeypatch):
    from spec_generator.worker_runtime import JobRuntime,job_runtime
    from spec_generator.artifact_lifecycle import ArchitectureLifecycle
    from delivery_api.tenancy import TenantAccessDenied,tenant_context
    from test_postgres_store import B
    with tenant_context(A):
        tid=store.create_task('life-projection-'+uuid.uuid4().hex)['task_id'];sid='life-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job);root=tmp_path/sid
        from spec_generator.model_config import StageModel
        runtime=JobRuntime({'needs':StageModel('fake','fake')},str(root/'calls.jsonl'),lambda text:None,documents=docs)
        runtime.bind_worker(owner_id=A.owner_id,task_id=tid,job_id=job,generation=cap.generation,session_root=root)
        with job_runtime(runtime):
            life=ArchitectureLifecycle(root/'spec',sid,{'id':'baseline'});life.begin_or_checkpoint({})
            for _ in range(50):life.begin_or_checkpoint({})
            content={'done':1};life.commit_finalize(life.finalize(content));assert life.state.value=='complete'
            life.resume('items',content=content)
            # A new lifecycle object and document capability use the durable latest pair.
            restarted=ArchitectureLifecycle(root/'spec',sid,{'id':'baseline'})
            restarted.resume('items',content=content)
            with pytest.raises(ValueError,match='sealed'):restarted.resume('items',content={'done':2})
            def no_history(*args,**kw):raise AssertionError('whole lifecycle read forbidden')
            monkeypatch.setattr(docs,'read',no_history)
            original=GeneratorDocuments._verified_bytes
            monkeypatch.setattr(GeneratorDocuments,'_verified_bytes',staticmethod(no_history))
            assert restarted.state.value=='partial'
            monkeypatch.setattr(GeneratorDocuments,'_verified_bytes',staticmethod(original))
            restarted.commit_finalize(restarted.finalize(content));assert restarted.state.value=='complete'
            docs.clear('spec/artifact-lifecycle.jsonl');assert restarted.state.value=='missing'
            restarted.begin_or_checkpoint({});assert restarted.state.value=='partial'
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):docs.recent_lifecycle_events('spec/artifact-lifecycle.jsonl',life.artifact_id)
        store.finish_worker(job)
        with pytest.raises(TenantAccessDenied):docs.recent_lifecycle_events('spec/artifact-lifecycle.jsonl',life.artifact_id)


def test_projection_constant_read_benchmark(store,capsys):
    import time,statistics
    from delivery_api.pg_usage import usage_rows,usage_summary
    with tenant_context(A):
        tid=store.create_task('usage-perf-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'usage-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:srs');docs=GeneratorDocuments(store,store.claim_worker(job),job)
        raw=json.dumps(dict(stage='srs',prompt_tokens=1,completion_tokens=2,provider='fake',model='fake')).encode()+b'\n'
        docs.write('spec/usage.jsonl',raw*10000);usage_summary(store,tid)
        def measure(fn):
            values=[]
            for _ in range(7):
                start=time.perf_counter();fn();values.append((time.perf_counter()-start)*1000)
            return round(statistics.median(values),3)
        old=measure(lambda:usage_rows(store,tid));new=measure(lambda:usage_summary(store,tid))
        print('\nUsage 10k local old/new median ms:',old,new)
        assert usage_summary(store,tid)['tokens']==[0,30000]
        artifact_id='architecture:'+docs.capability.session_id
        event=json.dumps({'artifact_id':artifact_id,'to_state':'partial'}).encode()+b'\n'
        key='spec/artifact-lifecycle.jsonl';docs.write(key,event*10000)
        docs.recent_lifecycle_events(key,artifact_id)
        old=measure(lambda:[json.loads(line) for line in docs.read(key).splitlines()])
        new=measure(lambda:docs.recent_lifecycle_events(key,artifact_id))
        print('Lifecycle 10k local old/new median ms:',old,new)
        assert len(docs.recent_lifecycle_events(key,artifact_id))==2


def test_projection_rollback_new_bad_chunk_and_tenant_visibility(store):
    from delivery_api.pg_usage import usage_summary
    from delivery_api.tenancy import TenantAccessDenied
    from test_postgres_store import B,DSN
    import psycopg
    with tenant_context(A):
        tid=store.create_task('usage-safe-'+uuid.uuid4().hex)['task_id'];sid='usage-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');docs=GeneratorDocuments(store,store.claim_worker(job),job)
        docs.append('spec/usage.jsonl',b'{"prompt_tokens":1}\n');initial=usage_summary(store,tid)
        with pytest.raises(RuntimeError):
            with store._task_operation_guard('task.read'):
                docs.append('spec/usage.jsonl',b'{"prompt_tokens":2}\n')
                assert usage_summary(store,tid)['tokens']==[0,3]
                raise RuntimeError('abort projection and append together')
        assert usage_summary(store,tid)==initial
        docs.append('spec/usage.jsonl',b'{"prompt_tokens":4}\n')
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE document_chunks SET payload=%s WHERE execution_session_id=%s AND ordinal=2',(b'bad',sid))
            for role in ('anon','authenticated','service_role'):
                assert not c.execute("SELECT has_table_privilege(%s,'document_projections','SELECT')",(role,)).fetchone()[0]
        with pytest.raises(ValueError,match='hash'):usage_summary(store,tid)
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):usage_summary(store,tid)
            with store._postgres.transaction() as c:assert c.execute('SELECT 1 FROM document_projections WHERE execution_session_id=%s',(sid,)).fetchone() is None
