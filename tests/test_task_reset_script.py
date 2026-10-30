"""Reset launcher is dry by default, owner-scoped and never bypasses Stop/ledger."""
import importlib.util
from pathlib import Path
import pytest

@pytest.fixture
def script():
    path=Path(__file__).parents[1]/'scripts/ops-reset-existing-tasks.py'
    spec=importlib.util.spec_from_file_location('reset_tasks_script',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def test_default_is_dry_and_never_writes(script):
    args=script.parse_args(['--owner','00000000-0000-0000-0000-000000000001','--auth-session','00000000-0000-0000-0000-0000000000a1'])
    assert not args.apply
    class Store:
        def list_tasks(self):return [{'task_id':'t','title':'old','state':'not_started','generation':1,'stop_requested_at':None}]
        def task_worker_state(self,tid,limit=100):return {'worker_jobs':[],'provider_attempts':[]}
        def delete_task(self,*a,**kw):raise AssertionError('dry run wrote')
    report=script.plan(Store())
    assert report['task_count']==1 and report['blocked']==[]


def test_plan_refuses_unresolved_work_and_does_not_release_money(script):
    class Store:
        def list_tasks(self):return [{'task_id':'t','title':'running','state':'running','generation':1,'stop_requested_at':None}]
        def task_worker_state(self,tid,limit=100):return {'worker_jobs':[{'job_id':'j','state':'running'}],'provider_attempts':[{'attempt_id':'a','state':'uncertain'}]}
    report=script.plan(Store())
    assert report['blocked'] and report['tasks'][0]['task_id']=='t'
    with pytest.raises(ValueError,match='unresolved'):script.apply(Store(),report,confirm=lambda prompt:'DELETE 1 TASKS')


def test_typed_confirmation_and_unchanged_plan_are_required(script):
    class Store:
        def __init__(self):self.deleted=[]
        def list_tasks(self):return [{'task_id':'t','title':'old','state':'not_started','generation':1,'stop_requested_at':None}]
        def task_worker_state(self,tid,limit=100):return {'worker_jobs':[],'provider_attempts':[]}
        def delete_task(self,tid,actor):self.deleted.append(tid)
    store=Store();report=script.plan(store)
    with pytest.raises(ValueError,match='confirmation'):script.apply(store,report,confirm=lambda prompt:'yes')
    assert store.deleted==[]
    script.apply(store,report,confirm=lambda prompt:'DELETE 1 TASKS')
    assert store.deleted==['t']

def test_partial_failure_is_reported_without_automatic_retry(script):
    class Store:
        def __init__(self):self.calls=[]
        def list_tasks(self):return [{'task_id':v,'title':'old','state':'not_started','generation':1,'stop_requested_at':None} for v in ('a','b')]
        def task_worker_state(self,tid,limit=100):return {'worker_jobs':[],'provider_attempts':[]}
        def delete_task(self,tid,actor):
            self.calls.append(tid)
            if tid=='b':raise ValueError('policy changed')
    store=Store()
    with pytest.raises(RuntimeError,match='after 1 task'):
        script.apply(store,script.plan(store),confirm=lambda prompt:'DELETE 2 TASKS')
    assert store.calls==['a','b']


def test_real_disposable_pg_plan_and_apply_use_existing_deletion_policy(script,store):
    import uuid
    from test_postgres_store import A
    from delivery_api.tenancy import tenant_context
    from types import SimpleNamespace
    with tenant_context(A):
        tid=store.create_task('reset-script-'+uuid.uuid4().hex)['task_id']
        scoped=SimpleNamespace(list_tasks=lambda:[store.task(tid)] if store.task(tid) else [],
            task_worker_state=store.task_worker_state,delete_task=store.delete_task)
        reviewed=script.plan(scoped)
        assert reviewed['task_count']==1 and reviewed['blocked']==[]
        script.apply(scoped,reviewed,confirm=lambda prompt:'DELETE 1 TASKS')
        assert store.task(tid) is None
        assert any(e['action']=='task.deleted' for e in store.audit_entries(tid))


def test_real_disposable_pg_unresolved_job_is_refused_before_delete(script,store):
    import uuid
    from test_postgres_store import A
    from delivery_api.tenancy import tenant_context
    from types import SimpleNamespace
    with tenant_context(A):
        tid=store.create_task('reset-blocked-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'reset-'+uuid.uuid4().hex)
        jid=store.enqueue_worker(tid,'phase:srs')
        scoped=SimpleNamespace(list_tasks=lambda:[store.task(tid)],task_worker_state=store.task_worker_state,delete_task=store.delete_task)
        reviewed=script.plan(scoped)
        assert reviewed['blocked'][0]['task_id']==tid
        with pytest.raises(ValueError,match='unresolved'):script.apply(scoped,reviewed,confirm=lambda prompt:'DELETE 1 TASKS')
        assert store.task(tid) is not None and store.worker_job(jid)['state']=='pending'
