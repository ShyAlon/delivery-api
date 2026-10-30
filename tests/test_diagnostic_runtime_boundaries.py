import logging
from delivery_api import diagnostic_evidence as d


def test_context_binding_restores_parent_and_logs_safe_failure(tmp_path,pg_log_evidence):
 path=tmp_path/'diagnostics.jsonl';h=d.EvidenceHandler(path);log=logging.getLogger('delivery_api.diagnostic');log.addHandler(h)
 try:
  with d.bound(task_id='t',session_id='s',job_id='j'):
   try:raise ValueError('password=secret')
   except ValueError:d.failure('worker.failed')
  assert d.context.get()=={}
 finally:log.removeHandler(h)
 assert 'worker.failed' in pg_log_evidence(task_id='t')['text']
 assert 'secret' not in pg_log_evidence()['text']
 assert 'session_id' in pg_log_evidence()['text']


def test_managed_log_budget_is_no_more_than_32mib(pg_log_evidence):
 from delivery_api import log_budget as b
 assert sum(size*files for size,files in b.BUDGET.values())<=32*1024*1024
 assert set(b.BUDGET)=={'diagnostics','activity','engine','stages'}


def test_buffer_failure_becomes_degraded_not_silent(monkeypatch,pg_log_evidence):
 from delivery_api import server_log_buffer as s,log_durability
 monkeypatch.setattr(log_durability,'_failures',{})
 class Broken:
  def append(self,row):raise OSError('private')
 import threading
 h=s._Buffer(Broken(),threading.Lock(),logging.WARNING)
 h.emit(logging.makeLogRecord({'msg':'test'}))
 assert log_durability.snapshot()['degraded']


def test_activity_inherits_correlation_without_overriding_explicit_fields(monkeypatch,pg_log_evidence):
 from delivery_api import observability as o
 lines=[]
 monkeypatch.setattr(o.logger,'log',lambda *a,**k:lines.append(a[1]))
 with d.bound(task_id='parent',session_id='s',job_id='j'):
  o.event('fixture','test',task_id='child')
 assert "task_id='child'" in lines[0] and "job_id='j'" in lines[0]
