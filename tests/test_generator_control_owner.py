"""The finished interview is not the active generator phase's liveness owner."""
from registered_runner import registered_runner
import time
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from delivery_api.tenancy import tenant_context
from delivery_api.runner import ActiveWork
from vmodel_contracts.interactive import EngineSnapshot,ProcessStatus,NextStep

@pytest.fixture
def live_task(pg_scoped_workspace,pg_task_app,monkeypatch):
 import delivery_api.app as m
 w=pg_scoped_workspace
 with tenant_context(w.scope):
  task=w.store.create_task('Generator after completed interview',task_id='generator-control-'+uuid4().hex[:8])
  sid=str(uuid4());w.store.set_engine_session(task['task_id'],sid)
  w.store.update_phase(task['task_id'],'intent','done');w.store.update_phase(task['task_id'],'conops','done')
  w.store.update_phase(task['task_id'],'needs','running',detail='Stakeholder Needs 1 out of 7',completed=1,total=7)
 calls=[]
 def completed(session):
  calls.append(session)
  return EngineSnapshot(session_id=sid,task_id=task['task_id'],sequence=28,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE)
 monkeypatch.setattr(m.engine_client,'snapshot',completed)
 monkeypatch.setattr(m.runner,'active_work',{})
 client=TestClient(pg_task_app(w),headers=w.headers)
 yield m,w,task['task_id'],sid,client,calls
 client.close()


def test_running_generator_survives_completed_interview(live_task):
 m,w,tid,sid,client,calls=live_task
 m.runner.active_work[tid]=ActiveWork('subprocess','needs','running',time.time(),durable_ref='123')
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']=='working',body['control_snapshot']
 assert body['control_snapshot']['process_live'] is True
 assert body['control_snapshot']['diagnostic'] is None
 assert body['active_work']['phase']=='needs' and body['input_state']['process_live'] is True
 assert calls==[],'a downstream phase must not query the retired interview owner'


def test_no_runner_does_not_invent_live_work_from_running_phase_row(live_task):
 m,w,tid,sid,client,calls=live_task
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']=='failed'
 assert body['control_snapshot']['process_live'] is False and body['active_work'] is None
 assert body['control_snapshot']['diagnostic']['reason']=='task has no live ActiveWork owner'


def test_stale_other_phase_work_is_not_an_owner(live_task):
 m,w,tid,sid,client,calls=live_task
 m.runner.active_work[tid]=ActiveWork('subprocess','srs','running',time.time())
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']=='failed' and body['active_work'] is None


def test_generator_waiting_decision_keeps_its_owner(live_task):
 m,w,tid,sid,client,calls=live_task
 decision={'decision_id':'decision-fixture','prompt':'Choose the intended scope','options':[{'value':'keep','label':'Keep'}]}
 m.runner.active_work[tid]=ActiveWork('decision','needs','waiting',time.time(),decision)
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']=='waiting_input'
 assert body['control_snapshot']['owner']=='delivery' and body['control_snapshot']['wait_id']=='decision-fixture'
 assert body['input_state']['decision']==decision


def test_generator_worker_state_refreshes_after_cached_task_read(live_task):
 m,w,tid,sid,client,calls=live_task
 m.runner.active_work[tid]=ActiveWork('subprocess','needs','running',time.time())
 assert client.get('/api/tasks/'+tid).json()['control_snapshot']['mode']=='working'
 m.runner.active_work.pop(tid)
 assert client.get('/api/tasks/'+tid).json()['control_snapshot']['mode']=='failed'


def test_actual_runner_registration_and_cleanup_owns_control(live_task,registered_runner,tmp_path,monkeypatch):
 """Real ProcessRunner and real local child stream, no provider or owner's process."""
 import asyncio,sys
 from delivery_api.runner import ProcessRunner
 m,w,tid,sid,client,calls=live_task
 runner=ProcessRunner(w.store);monkeypatch.setattr(m,'runner',runner)
 async def run():
  job=asyncio.create_task(runner.run(tid,[sys.executable,'-u','-c',
      'import time; print("fixture alive",flush=True); time.sleep(.3)'],cwd=tmp_path,phase='needs'))
  try:
   for _ in range(100):
    if runner.work_state(tid):break
    if job.done():await job
    await asyncio.sleep(.01)
   assert runner.work_state(tid) is not None
   body=client.get('/api/tasks/'+tid).json()
   assert body['control_snapshot']['mode']=='working',body['control_snapshot']
   assert body['active_work']['phase']=='needs'
  finally:await job
  assert runner.work_state(tid) is None
  body=client.get('/api/tasks/'+tid).json()
  assert body['active_work'] is None and not body['control_snapshot']['process_live']
 with tenant_context(w.scope):asyncio.run(run())


@pytest.mark.parametrize('state',['stop_requested','failed'])
def test_task_stop_or_failure_wins_over_registered_work(live_task,state):
 m,w,tid,sid,client,calls=live_task
 with tenant_context(w.scope):
  if state=='failed':w.store.update_phase(tid,'needs','failed',detail='fixture failure')
  else:w.store.request_task_stop(tid)
 m.runner.active_work[tid]=ActiveWork('subprocess','needs','running',time.time())
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']==state


def test_resource_wait_keeps_requirement_visible(live_task):
 m,w,tid,sid,client,calls=live_task
 requirement={'title':'Source model needed','format':'JSON','path':'private-fixture-path'}
 m.runner.active_work[tid]=ActiveWork('requirement','needs','waiting',time.time(),requirement=requirement,durable_ref='private-fixture-path')
 body=client.get('/api/tasks/'+tid).json()
 assert body['control_snapshot']['mode']=='working' and body['control_snapshot']['process_live']
 assert body['active_work']['requirement']==requirement
 assert 'durable_ref' not in body['active_work']




def test_other_owner_cannot_read_registered_generator(live_task,pg_workspace_factory):
 m,w,tid,sid,client,calls=live_task
 m.runner.active_work[tid]=ActiveWork('subprocess','needs','running',time.time())
 with pg_workspace_factory() as other:
  reply=client.get('/api/tasks/'+tid,headers=other.headers)
  assert reply.status_code==404
  assert 'active_work' not in reply.json()
