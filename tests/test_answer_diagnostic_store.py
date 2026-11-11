"""Real tenant/fence coverage for one best-effort answer diagnostic transaction."""
import json,uuid
import pytest
from delivery_api.tenancy import tenant_context


def test_diagnostic_flush_exact_bytes_one_transaction_and_stop_spool(pg_workspace_factory,tmp_path):
    from interview_engine.pg_pool import build_pool
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_documents import InterviewDocuments
    from interview_engine.document_diagnostics import answer_batch,bind,append,write
    from delivery_api.answer_trace import WireTrace
    with pg_workspace_factory() as w,tenant_context(w.scope):
        tid=w.store.create_task('diagnostic batch')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
        pool=build_pool(w.dsn.replace('user=sandbox','user=delivery_app'))
        try:
            store=PgSessionStore(pool,claims=(w.scope.owner_id,w.scope.session_id))
            claim=store.claim(sid,w.scope.owner_id,'diagnostic-test',60)
            docs=InterviewDocuments(store,claim,tid)
            docs.append('interview/log.jsonl',b'old\n')
            trace=WireTrace();trace.install()
            try:
                with answer_batch(tmp_path/'recovery',{'owner_id':w.scope.owner_id,'session_id':sid,'operation_id':'one'}):
                    with bind(docs,tmp_path/'session'):
                        append(tmp_path/'session/interview/log.jsonl','first\n')
                        append(tmp_path/'session/interview/log.jsonl','second\n')
                        write(tmp_path/'session/interview/debug/raw.txt','  exact raw\n')
                        docs.append('interview/usage.jsonl',b'{"usage":1}\n')
                assert sum(row['syncs'] for row in trace.snapshot())==5,trace.snapshot()
            finally:trace.restore()
            assert docs.read('interview/log.jsonl')==b'old\nfirst\nsecond\n'
            assert docs.read('interview/debug/raw.txt')==b'  exact raw\n'
            assert docs.read('interview/usage.jsonl')==b'{"usage":1}\n'
            with answer_batch(tmp_path/'recovery',{'owner_id':w.scope.owner_id,'session_id':sid,'operation_id':'stopped'}):
                with bind(docs,tmp_path/'session'):
                    append(tmp_path/'session/interview/log.jsonl','must stay local\n')
                    w.store.request_task_stop(tid)
            files=list((tmp_path/'recovery').glob('*.json'));assert len(files)==1
            assert json.loads(files[0].read_text())['identity']['operation_id']=='stopped'
            with w.admin() as c:
                raw=c.execute("SELECT d.payload || coalesce((SELECT string_agg(ch.payload,''::bytea ORDER BY ch.ordinal) FROM document_chunks ch WHERE ch.execution_session_id=d.execution_session_id AND ch.document_key=d.document_key AND ch.epoch=d.chunk_epoch),''::bytea) FROM generator_documents d WHERE execution_session_id=%s AND document_key='interview/log.jsonl'",(sid,)).fetchone()[0]
                assert bytes(raw)==b'old\nfirst\nsecond\n'
        finally:pool.close()
