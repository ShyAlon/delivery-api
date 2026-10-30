"""Stop-only reviewed scope, no invented quiescence or worker shortcuts."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
SPEC=importlib.util.spec_from_file_location('stop_batch',Path(__file__).parents[1]/'scripts/ops-stop-existing-tasks.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

class Store:
    def __init__(self):self.tasks=[dict(task_id='t',title='Old',state='running',generation=1)];self.writes=[]
    def list_tasks(self):return self.tasks
    def task_worker_state(self,tid,limit):return {'worker_jobs':[{'state':'running'}],'provider_attempts':[]}
    def engine_session(self,tid):return 's'
    def delete_task(self,*a,**kw):raise AssertionError('must never Delete')


def test_dry_plan_includes_failed_task_with_unresolved_worker_without_writes():
    s=Store();s.tasks[0]['state']='failed'
    assert m.plan(s)['tasks'][0]['task_id']=='t' and s.writes==[]


def test_confirmation_and_changed_plan_prevent_stop(monkeypatch):
    s=Store();p=m.plan(s)
    with pytest.raises(ValueError,match='confirmation'):m.apply(s,None,p,confirm=lambda _: 'yes')
    s.tasks[0]['generation']=2
    with pytest.raises(ValueError,match='plan changed'):m.apply(s,None,p,confirm=lambda _:'STOP 1 TASKS')


def test_batch_calls_existing_coordinator_and_leaves_unverified_blocked(monkeypatch):
    import delivery_api.cooperative_stop as c
    calls=[]
    def stop(store,engine,tid,*,reconcile):
        calls.append((tid,reconcile));raise RuntimeError('never surface private details')
    monkeypatch.setattr(c,'request_stop',stop)
    s=Store();s.tasks.append(dict(task_id='u',title='Pending',state='stop_requested',generation=2))
    results=m.apply(s,None,m.plan(s),confirm=lambda _:'STOP 2 TASKS')
    assert calls==[('t',False),('u',True)]
    assert all(x['status']=='blocked' for x in results) and 'private' not in str(results)


def test_stopped_and_pending_results_are_not_auto_deleted(monkeypatch):
    import delivery_api.cooperative_stop as c
    monkeypatch.setattr(c,'request_stop',lambda *a,**kw:dict(delete_allowed=False,state='stop_requested',pending_reason='Worker unresolved'))
    s=Store();assert m.apply(s,None,m.plan(s),confirm=lambda _:'STOP 1 TASKS')[0]['status']=='pending'


def test_stopped_confirmation_is_reported(monkeypatch):
    import delivery_api.cooperative_stop as c
    monkeypatch.setattr(c,'request_stop',lambda *a,**kw:dict(delete_allowed=True,state='stopped',pending_reason=None))
    s=Store();assert m.apply(s,None,m.plan(s),confirm=lambda _:'STOP 1 TASKS')[0]['status']=='stopped'


def test_absent_preview_refuses_before_any_stop(monkeypatch):
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:NS(returncode=1,stdout='',stderr='sensitive'))
    with pytest.raises(ValueError,match='preview unavailable'):m.PreviewEngine('requi-preview-exec','owner','session','dsn')
