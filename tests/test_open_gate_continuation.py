"""Open generator gates reserve the next owned job only after settlement."""
import pytest
from delivery_api import inprocess_engine as ie


def seed(ws,phase='srs'):
 s=ws.store;tid=s.create_task('Open gate fixture')['task_id'];s.set_engine_session(tid,'open-'+tid)
 s.set_model_default(tid,'ollama:phi4')
 s.update_phase(tid,phase,'running');job=s.enqueue_worker(tid,'phase:'+phase)
 s.claim_worker(job);s.set_gate_open(tid,'gate-'+phase,True)
 s.wait_at_gate(tid,phase)
 return s,tid,job


def test_open_gate_waits_for_settlement_then_reserves_once(pg_scoped_workspace):
 s,tid,job=seed(pg_scoped_workspace)
 assert s.prepare_open_gate_continuation(tid,'srs',job) is None
 s.finish_worker(job)
 result=s.prepare_open_gate_continuation(tid,'srs',job)
 assert result['phase']=='architecture' and result['options']['model_route']=='ollama:phi4'
 assert s.worker_job(result['job_id'])['state']=='pending'
 assert s.prepare_open_gate_continuation(tid,'srs',job) is None


@pytest.mark.parametrize('phase',['intent','conops','validation'])
def test_no_interview_or_terminal_continuation(pg_scoped_workspace,phase):
 s,tid,job=seed(pg_scoped_workspace,phase);s.finish_worker(job)
 assert s.prepare_open_gate_continuation(tid,phase,job) is None


def test_closed_or_stopped_gate_cannot_continue(pg_scoped_workspace):
 s,tid,job=seed(pg_scoped_workspace);s.finish_worker(job)
 s.set_gate_open(tid,'gate-srs',False)
 assert s.prepare_open_gate_continuation(tid,'srs',job) is None
 s.set_gate_open(tid,'gate-srs',True);s.request_task_stop(tid)
 assert s.prepare_open_gate_continuation(tid,'srs',job) is None


def test_lifecycle_releases_slot_before_continuation_reservation():
 import asyncio
 async def run():
  engine=ie.ChildPhaseLifecycle('/unused',gateway_factory=None);calls=[]
  async def one(store,orch,tid,phase,job,options):
   assert engine._slot is None or not engine._slot.locked()
   engine._slot=asyncio.Lock()
   async with engine._slot:calls.append((phase,job))
   return True
  engine._dispatch_one=one
  class Store:
   def prepare_open_gate_continuation(self,tid,phase,job):
    assert not engine._slot.locked() and ie.take(tid) is None
    if phase=='srs':return {'phase':'architecture','job_id':'next','options':{'mode':'start','model_route':'ollama:phi4'}}
  assert await engine.dispatch(Store(),None,'task','srs','first',{})
  assert calls==[('srs','first'),('architecture','next')]
 asyncio.run(run())


def test_pg_lifecycle_continues_with_fresh_claim_and_released_slot(pg_scoped_workspace):
 import asyncio
 from delivery_api.tenant_jobs import execute_job
 async def run():
  s=pg_scoped_workspace.store;tid=s.create_task('Actual open policy chain')['task_id']
  s.set_engine_session(tid,'chain-'+tid);s.set_model_default(tid,'ollama:phi4')
  s.set_gate_open(tid,'gate-srs',True)
  job=s.enqueue_worker(tid,'phase:srs',execution_options={'model_route':'ollama:phi4'})
  engine=ie.ChildPhaseLifecycle('/unused',gateway_factory=None);seen=[]
  class Worker:
   def __init__(self,proc):self.proc=proc
   async def execute(self,jid,producer):
    async def handler(cap):
     cap.validate(s);self.proc.started.set();producer(cap,None)
    return await execute_job(s,jid,handler)
  engine._worker=lambda store,proc:Worker(proc)
  engine._produce=lambda proc:lambda cap,root:proc.finish(0)
  class Orch:
   async def execute_phase(self,task_id,phase,**options):
    assert engine._slot.locked()
    if seen:assert s.worker_job(seen[-1][1])['state']=='done'
    # Worker returns quickly in this no-provider fixture, so exact claim is
    # established by its completed row if it finished before phase projection.
    with s._postgres.transaction() as conn:
     rows=conn.execute('SELECT job_id,state,attempt FROM worker_jobs WHERE task_id=%s AND kind=%s',(tid,'phase:'+phase)).fetchall()
    assert len(rows)==1 and rows[0]['attempt']==1
    seen.append((phase,rows[0]['job_id']))
    s.update_phase(tid,phase,'running');s.wait_at_gate(tid,phase)
  assert await engine.dispatch(s,Orch(),tid,'srs',job,{})
  assert [phase for phase,jid in seen]==['srs','architecture']
  assert all(s.worker_job(jid)['state']=='done' for phase,jid in seen)
  assert s.task(tid)['state']=='waiting_gate'
  assert not engine._slot.locked() and ie.take(tid) is None
 asyncio.run(run())
