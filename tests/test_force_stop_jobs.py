"""Forced closure is an audited override, never accounting or exit proof."""
import json,uuid
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B


@pytest.fixture(autouse=True)
def isolated_force_evidence():
    yield
    from test_attempt_ledger import DSN
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE worker_jobs SET state='failed' WHERE task_id IN (SELECT task_id FROM tasks WHERE title LIKE 'Force review %') AND state IN ('running','pending','paused')")
        c.execute("DELETE FROM provider_attempts WHERE job_id IN (SELECT job_id FROM worker_jobs WHERE task_id IN (SELECT task_id FROM tasks WHERE title LIKE 'Force review %'))")


def job(store,hidden=False):
    t=store.create_task('Force review '+uuid.uuid4().hex)
    store.set_engine_session(t['task_id'],str(uuid.uuid4()))
    j=store.enqueue_worker(t['task_id'],'phase:intent');store.claim_worker(j)
    if hidden:store.stop_hide_worker_task(j,confirm=True)
    return t['task_id'],j


def test_hidden_job_force_records_override_preserves_tombstone_and_never_claims_exit(store):
    with tenant_context(A):
        tid,j=job(store,True)
        before=store.force_stop_jobs_plan();assert j in {r['job_id'] for r in before['jobs']}
        with pytest.raises(ValueError):store.force_stop_jobs_apply(before['fingerprint'],'yes')
        assert store.worker_job(j)['state']=='running'
        result=store.force_stop_jobs_apply(before['fingerprint'],'FORCE STOP '+str(len(before['jobs']))+' JOBS')
        assert any(r['job_id']==j and not r['worker_exit_verified'] for r in result['results'])
        assert store.worker_job(j)['state']=='failed' and store.task(tid) is None
        audit=next(r for r in store.audit_entries(tid) if r['action']=='worker.force_stop')
        detail=audit['detail'];detail=json.loads(detail) if isinstance(detail,str) else detail
        assert detail['forced_override'] and not detail['worker_exit_verified'] and not detail['accounting_changed']
        with store._postgres.transaction() as c:
            row=c.execute('SELECT generation,deleted_at,stopped_at FROM tasks WHERE task_id=%s',(tid,)).fetchone()
            assert row['deleted_at'] and row['stopped_at'] is None and row['generation']==3
        assert j not in {r['job_id'] for r in store.force_stop_jobs_plan()['jobs']}


def test_force_is_owner_scoped_and_stale_plan_is_atomic(store):
    with tenant_context(A):
        tid,j=job(store);review=store.force_stop_jobs_plan();tid2,j2=job(store)
        with pytest.raises(ValueError,match='plan changed'):store.force_stop_jobs_apply(review['fingerprint'],'FORCE STOP '+str(len(review['jobs']))+' JOBS')
        assert store.worker_job(j)['state']=='running'
    with tenant_context(B):
        other=store.force_stop_jobs_plan();assert j not in {r['job_id'] for r in other['jobs']}
        with pytest.raises(ValueError):store.force_stop_jobs_apply(review['fingerprint'],'FORCE STOP '+str(len(review['jobs']))+' JOBS')


def test_accounting_is_unchanged_even_for_unsettled_attempt(store):
    from test_attempt_ledger import Q,DSN
    import psycopg
    with tenant_context(A):
        tid,j=job(store)
        with psycopg.connect(DSN) as c:
            c.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2) ON CONFLICT(owner_id) DO NOTHING',(A.owner_id,'force-test'))
        aid=uuid.uuid4().hex;store.reserve_provider_attempt(j,aid,Q,'force-test')
        store.transition_provider_attempt(aid,'start')
        before=store.provider_attempt(aid)
        review=store.force_stop_jobs_plan()
        result=store.force_stop_jobs_apply(review['fingerprint'],'FORCE STOP '+str(len(review['jobs']))+' JOBS')
        assert store.provider_attempt(aid)==before and before['state']=='dispatched'
        assert any(x['id']==aid and x['state']=='dispatched' for x in result['accounting'])


def test_audit_failure_rolls_back_entire_override(store,monkeypatch):
    with tenant_context(A):
        tid,j=job(store,True);review=store.force_stop_jobs_plan()
        def fail(*args,**kwargs):raise RuntimeError('audit unavailable')
        monkeypatch.setattr(store,'audit',fail)
        with pytest.raises(RuntimeError):store.force_stop_jobs_apply(review['fingerprint'],'FORCE STOP '+str(len(review['jobs']))+' JOBS')
        assert store.worker_job(j)['state']=='running'


def test_script_is_dry_by_default_and_requires_exact_confirmation():
    import ast
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/owner/force-stop-all.py').read_text()
    ast.parse(text)
    assert "'--apply',action='store_true'" in text
    assert text.index('if not args.apply:')<text.index('store.force_stop_jobs_apply(')
    assert 'sys.stdin.isatty()' in text and 'UNVERIFIED' in text


def test_force_preflight_names_missing_update_before_apply(store):
    from test_attempt_ledger import DSN
    import psycopg
    with tenant_context(A):
        tid,j=job(store)
        with psycopg.connect(DSN) as c:c.execute('REVOKE UPDATE ON worker_jobs FROM authenticated')
        try:
            with pytest.raises(ValueError,match='worker_jobs'):store.force_stop_jobs_plan()
            assert store.worker_job(j)['state']=='running'
        finally:
            with psycopg.connect(DSN) as c:c.execute('GRANT UPDATE ON worker_jobs TO authenticated')


def test_force_preflight_names_legacy_generation_binding(store):
    from test_attempt_ledger import DSN
    import psycopg
    with tenant_context(A):
        tid,j=job(store,True)
        with psycopg.connect(DSN) as c:c.execute('UPDATE engine_sessions SET generation=99 WHERE task_id=%s',(tid,))
        review=store.force_stop_jobs_plan()
        assert j in review['fenced_repair_ids']
        assert j in review['confirmation']
        store.force_stop_jobs_apply(review['fingerprint'],review['confirmation'])
        assert store.worker_job(j)['state']=='failed'


def test_denial_context_never_leaks_raw_driver_text():
    from delivery_api.force_stop_jobs import execute
    class Denied(Exception):sqlstate='42501'
    class Store:
        def _execute_native(self,*args,**kwargs):raise Denied('password=DO-NOT-PRINT')
    with pytest.raises(ValueError) as exc:execute(Store(),'tasks','UPDATE stop fence','ignored')
    assert 'tasks UPDATE stop fence' in str(exc.value) and '42501' in str(exc.value)
    assert 'DO-NOT-PRINT' not in str(exc.value)


def test_fenced_function_refuses_foreign_owner_and_accounting_and_is_not_public(store):
    from test_attempt_ledger import DSN
    import psycopg
    with tenant_context(A):
        tid,j=job(store,True)
        with psycopg.connect(DSN) as c:c.execute('UPDATE engine_sessions SET generation=2 WHERE task_id=%s',(tid,))
        review=store.force_stop_jobs_plan()
        with pytest.raises(ValueError):store.force_stop_jobs_apply(review['fingerprint'],'FORCE STOP '+str(len(review['jobs']))+' JOBS')
        with store._postgres.transaction() as c:
            assert not c.execute("SELECT has_function_privilege('anon','public.requi_force_close_fenced_job(text,text,text)','EXECUTE') AS allowed").fetchone()['allowed']
    with tenant_context(B),store._postgres.transaction() as c:
        with pytest.raises(Exception):c.execute('SELECT public.requi_force_close_fenced_job(%s,%s,%s)',(j,'FORCE CLOSE '+j,'a'*64))
    with tenant_context(A):assert store.worker_job(j)['state']=='running'


def test_newer_binding_force_preserves_task_and_has_durable_false_exit_audit(store):
    from test_attempt_ledger import DSN
    import psycopg
    with tenant_context(A):
        tid,j=job(store,True)
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE engine_sessions SET generation=2 WHERE task_id=%s',(tid,))
            before=c.execute('SELECT generation,deleted_at,stopped_at FROM tasks WHERE task_id=%s',(tid,)).fetchone()
        review=store.force_stop_jobs_plan();store.force_stop_jobs_apply(review['fingerprint'],review['confirmation'])
        with psycopg.connect(DSN) as c:assert c.execute('SELECT generation,deleted_at,stopped_at FROM tasks WHERE task_id=%s',(tid,)).fetchone()==before
        evidence=next(r for r in store.audit_entries(tid) if r['action']=='worker.force_close_fenced')
        detail=evidence['detail'];detail=json.loads(detail) if isinstance(detail,str) else detail
        assert detail['forced_override'] and not detail['worker_exit_verified']
        assert detail['binding_generation']==2 and detail['job_generation']==1
