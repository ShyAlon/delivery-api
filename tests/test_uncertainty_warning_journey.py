"""Real shared ledger admission, with no real provider or live database."""
import uuid
from test_attempt_ledger import ledger,Q
from test_postgres_store import A,DSN
from delivery_api.tenancy import tenant_context

def test_uncertain_job_preserved_paid_and_free_admitted_and_audited(ledger):
    s,tid,old_job=ledger
    with tenant_context(A):
        old=uuid.uuid4().hex
        s.reserve_provider_attempt(old_job,old,Q,'unknown-call')
        s.transition_provider_attempt(old,'start')
        before=s.transition_provider_attempt(old,'uncertain')
        s.transition_provider_attempt(old,'uncertain')
        task=s.create_task('independent-'+uuid.uuid4().hex);new_tid=task['task_id']
        s.set_engine_session(new_tid,'session-'+uuid.uuid4().hex)
        job=s.enqueue_worker(new_tid,'phase:needs');s.claim_worker(job)
        try:
            local=Q|{'provider':'ollama','expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0}
            for quote in (local,Q):
                aid=uuid.uuid4().hex
                assert s.reserve_provider_attempt(job,aid,quote,'independent')['state']=='reserved'
                assert s.transition_provider_attempt(aid,'start')['state']=='dispatched'
                assert s.transition_provider_attempt(aid,'settle',{'input_tokens':1,'output_tokens':1})['state']=='settled'
            assert s.provider_attempt(old)==before
            entries=[a for a in s.audit_entries(tid) if a['action']=='provider.attempt.uncertain']
            assert len(entries)==1 and entries[0]['detail']['admission']=='warning_only'
            assert entries[0]['detail']['automatic_retry'] is False
        finally:
            import psycopg
            with psycopg.connect(DSN) as c:
                c.execute('DELETE FROM provider_attempts WHERE job_id=%s',(job,))
            s.finish_worker(job)
            s.delete_task(new_tid)
