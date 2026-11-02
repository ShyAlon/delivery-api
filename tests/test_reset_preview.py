"""Read-only reset planning while target/accounting consent remains unresolved."""
import importlib.util,uuid
from pathlib import Path
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B,DSN


def module():
    path=Path(__file__).parents[1]/'scripts/owner/reset-preview.py'
    spec=importlib.util.spec_from_file_location('reset_preview',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_inventory_includes_hidden_owner_tasks_and_never_mutates(store):
    import psycopg
    m=module()
    with tenant_context(A):
        t=store.create_task('Reset inventory '+uuid.uuid4().hex)
        store.set_engine_session(t['task_id'],str(uuid.uuid4()))
        j=store.enqueue_worker(t['task_id'],'phase:intent');store.claim_worker(j)
        store.stop_hide_worker_task(j,confirm=True)
        with psycopg.connect(DSN,options='-c default_transaction_read_only=on') as c:
            rows=m.inventory(c,A.owner_id)
            tables={r['table']:r for r in rows}
            assert tables['tasks']['action']=='DELETE CANDIDATE' and tables['worker_jobs']['rows']>=1
            assert store.worker_job(j)['state']=='running' and store.task(t['task_id']) is None
        with store._postgres.transaction() as c:c.execute("UPDATE worker_jobs SET state='failed' WHERE job_id=%s",(j,))


def test_apply_requires_exact_reviewed_target_and_owner():
    import pytest
    with pytest.raises(ValueError,match='reviewed owner'):module().reset(DSN,B.owner_id,'wipe')


def test_target_inventory_lists_other_owners_without_claiming_disposable():
    import psycopg
    with psycopg.connect(DSN,options='-c default_transaction_read_only=on') as c:
        result=module().target_evidence(c,DSN,A.owner_id)
        assert result['host_kind']=='local'
        auth=result['owner_inventory'][0]
        assert auth['distinct_owners']>=2 and any(not row['is_selected_owner'] for row in auth['owners'])
        assert not result['only_selected_auth_user']


def test_statistics_zip_is_verified_and_owner_scoped(tmp_path):
    import psycopg,json,zipfile,hashlib
    with psycopg.connect(DSN,options='-c default_transaction_read_only=on') as c:
        out=module().snapshot(c,A.owner_id,tmp_path)
        path=Path(out['path'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==out['sha256']
        with zipfile.ZipFile(path) as z:
            data=json.loads(z.read('statistics.json'))
            assert data['owner_id']==A.owner_id and 'provider_by_session_stage' in data
            assert 'caveats' in data and 'retail_by_session_stage' in data


def test_fk_order_puts_children_before_parent():
    import psycopg
    with psycopg.connect(DSN,options='-c default_transaction_read_only=on') as c:
        order=module().deletion_order(c,[{'table':x,'action':'DELETE CANDIDATE'} for x in ('tasks','engine_sessions','worker_jobs','provider_attempts','credit_charges')],'wipe')
        assert order.index('credit_charges')<order.index('provider_attempts')<order.index('worker_jobs')<order.index('engine_sessions')<order.index('tasks')


def test_complete_owner_reset_keeps_other_owner_balance_and_audit(store,tmp_path,monkeypatch):
    import psycopg
    m=module();monkeypatch.setattr(m,'APPROVED_OWNER',A.owner_id)
    real=m.target_evidence
    monkeypatch.setattr(m,'target_evidence',lambda c,d,o:real(c,d,o)|{'supabase_project_ref':m.APPROVED_PROJECT})
    for scope in (A,B):
        with tenant_context(scope):
            t=store.create_task('Reset full '+uuid.uuid4().hex);store.set_engine_session(t['task_id'],str(uuid.uuid4()))
            j=store.enqueue_worker(t['task_id'],'phase:intent');store.claim_worker(j)
            if scope==A:
                from test_attempt_ledger import Q
                with psycopg.connect(DSN) as c:c.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2) ON CONFLICT(owner_id) DO NOTHING',(A.owner_id,'reset-test'))
                aid=uuid.uuid4().hex;store.reserve_provider_attempt(j,aid,Q,'reset-test');store.transition_provider_attempt(aid,'start')
                store.stop_hide_worker_task(j,confirm=True)
    with psycopg.connect(DSN) as c:
        run='reset-run-'+uuid.uuid4().hex
        c.execute("INSERT INTO runs(run_id,state,budget_usd,created_at,updated_at,owner_id) VALUES(%s,'done',0,0,0,%s)",(run,A.owner_id))
        c.execute("INSERT INTO links(token,run_id,founder_label,created_at) VALUES(%s,%s,'reset fixture',0)",(uuid.uuid4().hex,run))
        other=c.execute('SELECT count(*) FROM tasks WHERE owner_id=%s',(B.owner_id,)).fetchone()[0]
    m.reset(DSN,A.owner_id,'wipe',confirm=lambda _:f'RESET PREVIEW {m.APPROVED_PROJECT} OWNER {A.owner_id} WIPE ACCOUNTING',directory=tmp_path)
    with psycopg.connect(DSN) as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE owner_id=%s',(A.owner_id,)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM tasks WHERE owner_id=%s',(B.owner_id,)).fetchone()[0]==other
        assert c.execute("SELECT count(*) FROM audit_log WHERE action='preview.owner_reset' AND owner_id=%s",(A.owner_id,)).fetchone()[0]==1
    assert list(tmp_path.glob('*.zip'))


def test_snapshot_failure_prevents_deletion(store,tmp_path,monkeypatch):
    import psycopg,pytest
    m=module();monkeypatch.setattr(m,'APPROVED_OWNER',A.owner_id)
    monkeypatch.setattr(m,'target_evidence',lambda *args:{'supabase_project_ref':m.APPROVED_PROJECT})
    with tenant_context(A):t=store.create_task('Snapshot refusal '+uuid.uuid4().hex)
    def fail(*a,**kw):raise OSError('disk full')
    monkeypatch.setattr(m,'snapshot',fail)
    with pytest.raises(OSError):m.reset(DSN,A.owner_id,'wipe',confirm=lambda _:f'RESET PREVIEW {m.APPROVED_PROJECT} OWNER {A.owner_id} WIPE ACCOUNTING',directory=tmp_path)
    with tenant_context(A):assert store.task(t['task_id'])


def test_confirmation_is_after_exclusive_lock_and_before_snapshot():
    import inspect
    text=inspect.getsource(module().reset)
    assert text.index('LOCK TABLE')<text.index("if confirm(")<text.index('exported=snapshot(')


def test_failed_table_lock_never_prompts_or_snapshots(store,monkeypatch,tmp_path):
    import psycopg,pytest
    from delivery_api.db_lock_diagnostics import inspect
    m=module();monkeypatch.setattr(m,'target_evidence',lambda *a:{'supabase_project_ref':m.APPROVED_PROJECT})
    def forbidden(*args):raise AssertionError('confirmation or snapshot before successful table lock')
    monkeypatch.setattr(m,'snapshot',forbidden)
    with psycopg.connect(DSN,application_name='reset-blocker') as holder:
        holder.execute('LOCK TABLE public.tasks IN ROW EXCLUSIVE MODE')
        pid=holder.execute('SELECT pg_backend_pid()').fetchone()[0]
        with pytest.raises(psycopg.errors.LockNotAvailable):m.reset(DSN,m.APPROVED_OWNER,'wipe',confirm=forbidden,directory=tmp_path)
        with psycopg.connect(DSN,options='-c default_transaction_read_only=on') as reader:
            assert any(r['pid']==pid and r['application_name']=='reset-blocker' for r in inspect(reader)['sessions'])
        assert not list(tmp_path.iterdir())
