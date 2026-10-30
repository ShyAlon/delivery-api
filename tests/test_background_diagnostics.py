import asyncio,logging
import pytest
from delivery_api import diagnostic_evidence as d


def test_failed_task_keeps_scope_and_safe_trace(tmp_path,file_log_evidence):
 path=tmp_path/'evidence';h=d.EvidenceHandler(path);log=logging.getLogger('delivery_api.diagnostic');log.addHandler(h)
 async def run():
  async def broken():raise ValueError('private-payload')
  with d.bound(task_id='t',job_id='j'):
   task=d.spawn(broken())
  await asyncio.sleep(0);await asyncio.sleep(0)
  with pytest.raises(ValueError):await task
 try:asyncio.run(run())
 finally:log.removeHandler(h);h.close()
 result=file_log_evidence(task_id='t')['text']
 assert 'background.task.failed' in result and 'job_id' in result and 'ValueError' in result
 assert 'private-payload' not in result


def test_cancelled_task_is_not_failure(tmp_path):
 path=tmp_path/'evidence';h=d.EvidenceHandler(path);log=logging.getLogger('delivery_api.diagnostic');log.addHandler(h)
 async def run():
  task=d.spawn(asyncio.sleep(10));task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
 try:asyncio.run(run())
 finally:log.removeHandler(h);h.close()
 assert d.read(path)['count']==0


def test_thread_wrapper_keeps_scope_and_exception(tmp_path,file_log_evidence):
 import threading
 path=tmp_path/'evidence';h=d.EvidenceHandler(path);log=logging.getLogger('delivery_api.diagnostic');log.addHandler(h);errors=[]
 def broken():raise RuntimeError('private-payload')
 with d.bound(task_id='t'):target=d.thread_target(broken)
 def run():
  try:target()
  except RuntimeError:errors.append('original')
 try:
  thread=threading.Thread(target=run);thread.start();thread.join()
 finally:log.removeHandler(h);h.close()
 result=file_log_evidence(task_id='t')['text']
 assert errors==['original'] and 'background.thread.failed' in result
 assert 'private-payload' not in result
