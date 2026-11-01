import logging
import pytest
from delivery_api import diagnostic_evidence as d


def test_warning_error_survives_new_reader_and_scopes_task(tmp_path,file_log_evidence):
 path=tmp_path/'diagnostics.jsonl';handler=d.EvidenceHandler(path,max_bytes=1024,backups=2)
 token=d.context.set({'request_id':'ref1','task_id':'task-a','session_id':'session-a'})
 try:
  handler.emit(logging.makeLogRecord({'name':'fixture','levelno':logging.WARNING,'levelname':'WARNING','msg':'failed password=secret-value'}))
 finally:d.context.reset(token)
 got=file_log_evidence(task_id='task-a')
 assert got['available'] and got['count']==1 and 'secret-value' not in got['text']
 assert 'ref1' in got['text'] and 'session-a' in got['text']
 assert file_log_evidence(task_id='task-other')['count']==0


def test_db_failure_never_records_query_values_or_exception_detail(monkeypatch,file_log_evidence):
 from delivery_api.postgres_scope import TimingConnection
 records=[];monkeypatch.setattr(d,'record',lambda *a,**k:records.append((a,k)))
 class Failure(Exception):sqlstate='23505'
 class Connection:
  def execute(self,*a):raise Failure('private literal and password=secret')
 with pytest.raises(Failure):TimingConnection(Connection()).execute("INSERT INTO x VALUES ('private inline')",('private parameter',))
 assert len(records)==1
 assert '23505' in str(records) and 'unique' in str(records).lower()
 assert 'private' not in str(records) and 'secret' not in str(records)


def test_pg_retention_read_is_bounded_and_reports_truncation(tmp_path,file_log_evidence,monkeypatch):
 path=tmp_path/'diagnostics.jsonl';h=d.EvidenceHandler(path,max_bytes=400,backups=2)
 from delivery_api import process_logs as postgres_logs
 original=file_log_evidence.repository.append
 monkeypatch.setattr(postgres_logs,'retention',lambda stream:1200)
 monkeypatch.setattr(postgres_logs,'selected',lambda:file_log_evidence.repository)
 for i in range(20):h.emit(logging.makeLogRecord({'name':'x','levelno':40,'levelname':'ERROR','msg':'failure '+str(i)}))
 assert not list(tmp_path.glob('diagnostics.jsonl*'))
 from delivery_api.process_logs import flush; assert flush(5)
 rows=file_log_evidence.repository.tail('diagnostics')
 assert 0<len(rows) and sum(len(r['text'].encode()) for r in rows)<=1200
 result=file_log_evidence(max_lines=2)
 assert result['count']==2 and result['truncated'] and result['scope']=='operator diagnostic evidence'


def test_debug_reader_requires_operator(monkeypatch,file_log_evidence):
 from delivery_api import app as m
 from fastapi import HTTPException
 from types import SimpleNamespace
 with pytest.raises(HTTPException) as error:m.debug_evidence(None,SimpleNamespace(admin=False))
 assert error.value.status_code==403


def test_http_noise_policy_keeps_errors_and_slow_requests(file_log_evidence):
 from delivery_api.app import _log_request
 assert not _log_request('GET','/health',200,12)
 assert not _log_request('GET','/api/tasks/task-a',200,12)
 assert not _log_request('POST','/api/client-log',204,12)
 assert _log_request('GET','/health',503,12)
 assert _log_request('GET','/api/tasks/task-a',200,1500)
 assert _log_request('POST','/api/tasks/task-a/answer',200,12)


def test_python_warning_is_durably_recorded(tmp_path,file_log_evidence):
 import warnings
 path=tmp_path/'diagnostics.jsonl';handler=d.EvidenceHandler(path)
 logger=logging.getLogger('py.warnings');logger.addHandler(handler)
 try:
  with warnings.catch_warnings():
   logging.captureWarnings(False);logging.captureWarnings(True)
   warnings.simplefilter('always');warnings.warn('synthetic warning',UserWarning)
 finally:logger.removeHandler(handler)
 assert 'synthetic warning' in file_log_evidence()['text']


def test_exception_chain_records_shapes_not_values(tmp_path,file_log_evidence):
 import sys,json
 handler=d.EvidenceHandler(tmp_path/'unused.jsonl')
 try:
  try:raise KeyError('PRIVATE-ROOT')
  except KeyError as cause:raise RuntimeError('PRIVATE-WRAPPER') from cause
 except RuntimeError:
  with d.bound(task_id='chain-task'):
   handler.emit(logging.makeLogRecord({'name':'fixture','levelno':40,'levelname':'ERROR','msg':'chain.failed','exc_info':sys.exc_info()}))
 result=file_log_evidence()['text']
 assert 'KeyError' in result and 'RuntimeError' in result and 'causes' in result
 assert 'PRIVATE-ROOT' not in result and 'PRIVATE-WRAPPER' not in result


def test_task_window_exports_more_than_400_exact_task_or_session_rows(monkeypatch):
 from contextlib import contextmanager
 from delivery_api import process_logs as p
 class Reader:
  @contextmanager
  def _read(self,*args):yield None
 class Logs:
  def records(self,**kwargs):
   assert kwargs['task_id']=='t' and kwargs['session_id']=='s' and kwargs['limit']==100000
   return {'records':[{'stream':'engine','ts':i,'text':'safe','session_id':'s'}for i in range(501)],'truncated':False,'unreadable':[]}
 monkeypatch.setattr(p,'selected',lambda:Logs())
 result=p.task_window(Reader(),None,'t','s',0,until=500)
 assert len(result['records'])==501 and not result['truncated'] and result['omissions']


def test_deterministic_exception_scan_discards_values_and_source():
 from delivery_api.process_logs import exception_blocks
 rows=[{'stream':'engine','ts':1,'text':line}for line in [
  'Traceback (most recent call last):','  File "/private/root/worker.py", line 42, in run',
  '    raise ValueError(secret_password)','ValueError: PRIVATE-SECRET']]
 result=exception_blocks(rows)
 assert result['found']==1 and result['blocks'][0]['frames']==[{'file':'worker.py','line':42,'function':'run'}]
 assert result['blocks'][0]['error_class']=='ValueError'
 assert 'PRIVATE-SECRET' not in str(result) and 'secret_password' not in str(result) and '/private/' not in str(result)


def test_task_window_rejects_unauthorized_reader(monkeypatch):
 from contextlib import contextmanager
 from delivery_api import process_logs as p
 class Reader:
  @contextmanager
  def _read(self,*args):raise PermissionError('no logs.read grant');yield
 monkeypatch.setattr(p,'selected',lambda:pytest.fail('must authorize before reading'))
 with pytest.raises(PermissionError):p.task_window(Reader(),None,'t','s',0)


def test_task_window_file_scope_includes_bound_session_not_other_task(tmp_path,monkeypatch):
 from delivery_api import process_logs as p
 monkeypatch.setattr(p,'root',lambda:tmp_path)
 repo=p.FileLogs()
 repo.append('engine','safe',session_id='mine',ts=1)
 repo.append('activity','safe',task_id='t',ts=2)
 repo.append('engine','private',session_id='other',ts=3)
 got=repo.records(task_id='t',session_id='mine',limit=100000)
 assert len(got['records'])==2 and 'private' not in str(got)
