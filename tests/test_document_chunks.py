import uuid,psycopg,pytest
from test_postgres_store import store,A,DSN
from delivery_api.tenancy import tenant_context
from delivery_api.generator_documents import GeneratorDocuments

def test_append_keeps_base_bytes_fixed_and_verifies_ordered_chunks(store):
    with tenant_context(A):
        tid=store.create_task('chunk-'+uuid.uuid4().hex)['task_id'];sid='chunk-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/log.txt',b'base')
        for n in range(100):docs.append('spec/log.txt',str(n).encode()+b'\n')
        expected=b'base'+b''.join(str(n).encode()+b'\n' for n in range(100))
        assert docs.read('spec/log.txt')==expected
        assert docs.capture(['spec/log.txt'])=={'spec/log.txt':expected}
        with psycopg.connect(DSN) as c:
            assert c.execute('SELECT payload FROM generator_documents WHERE execution_session_id=%s AND document_key=%s',(sid,'spec/log.txt')).fetchone()[0]==b'base'
            assert c.execute('SELECT count(*) FROM document_chunks WHERE execution_session_id=%s',(sid,)).fetchone()[0]==100
        docs.clear('spec/log.txt');docs.append('spec/log.txt',b'new');assert docs.read('spec/log.txt')==b'new'
        docs.write('spec/log.txt',b'replaced');assert docs.read('spec/log.txt')==b'replaced'
        docs.append('spec/log.txt',b'last')
        with psycopg.connect(DSN) as c:c.execute('UPDATE document_chunks SET payload=%s WHERE execution_session_id=%s',(b'tampered',sid))
        with pytest.raises(ValueError,match='hash'):docs.read('spec/log.txt')

def test_chunk_append_rollback_freshness_stop_and_permissions(store):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.tenancy import TenantAccessDenied
    with tenant_context(A):
        tid=store.create_task('chunk-safe-'+uuid.uuid4().hex)['task_id'];sid='chunk-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.append('spec/log.txt',b'first')
        assert docs.read_fresh('spec/log.txt',not_before=0)==b'first'
        assert docs.read_fresh('spec/log.txt',not_before=10**20) is None
        with pytest.raises(RuntimeError):
            with docs.saver._cursor() as c:
                c.execute('SELECT requi_document_append(%s,%s,%s,%s,%s,%s,0)',(sid,cap.generation,'spec/log.txt',b'rollback',A.owner_id,A.session_id))
                raise RuntimeError('abort')
        assert docs.read('spec/log.txt')==b'first'
        with psycopg.connect(DSN) as c:
            for role in ('anon','authenticated','service_role'):
                assert not c.execute("SELECT has_table_privilege(%s,'document_chunks','SELECT')",(role,)).fetchone()[0]
        store.finish_worker(job)
        with pytest.raises(TenantAccessDenied):docs.append('spec/log.txt',b'late')

def test_append_never_transfers_old_blob_to_parent(store,monkeypatch):
    import time,statistics
    with tenant_context(A):
        tid=store.create_task('chunk-perf-'+uuid.uuid4().hex)['task_id'];sid='chunk-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:srs');cap=store.claim_worker(job);docs=GeneratorDocuments(store,cap,job)
        docs.write('spec/log.txt',b'x'*1000000)
        def denied(*a,**kw):raise AssertionError('append read old bytes')
        monkeypatch.setattr(docs,'_verified_bytes',denied)
        times=[]
        for _ in range(30):
            start=time.perf_counter();docs.append('spec/log.txt',b'x'*1000);times.append((time.perf_counter()-start)*1000)
        print('\nChunk append local 1MB base +30x1k median ms:',round(statistics.median(times),3))
        with psycopg.connect(DSN) as c:
            size,count=c.execute('SELECT octet_length(payload),chunk_count FROM generator_documents WHERE execution_session_id=%s',(sid,)).fetchone()
            assert size==1000000 and count==30
