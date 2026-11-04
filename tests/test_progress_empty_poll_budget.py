"""Count current empty drain cost before optimizing, on disposable owned PG."""
import asyncio
from uuid import uuid4
from delivery_api.postgres_scope import RequestStats,request_stats
from vmodel_contracts.progress_signal import ProgressPage


def test_empty_poll_before_after(pg_scoped_workspace,monkeypatch):
 from delivery_api import app as module
 s=pg_scoped_workspace.store;t=s.create_task('Empty poll measurement');sid=str(uuid4())
 monkeypatch.setattr(module,'store',s)
 class Engine:
  def progress(self,session,cursor):return ProgressPage(events=[],next_cursor=cursor)
 monkeypatch.setattr(module,'engine_client',Engine())
 stats=RequestStats();token=request_stats.set(stats)
 try:asyncio.run(module._drain_engine_progress(t['task_id'],sid))
 finally:request_stats.reset(token)
 print('EMPTY_POLL_CURRENT',stats.queries,stats.transactions)
 assert stats.queries==2
 assert stats.transactions==1
 # Original empty ingestion did3extra queries/1transaction with no changes.
 baseline=RequestStats();token=request_stats.set(baseline)
 try:s.ingest_interview_progress_page(t['task_id'],sid,ProgressPage(events=[],next_cursor=0))
 finally:request_stats.reset(token)
 print('EMPTY_POLL_BEFORE_AFTER',stats.queries+baseline.queries,stats.queries,stats.transactions+baseline.transactions,stats.transactions)
 assert baseline.queries==3


def test_empty_poll_rejects_invented_cursor(monkeypatch):
 from delivery_api import app as module
 class Store:
  def progress_cursor(self,*args):return 0
 class Engine:
  def progress(self,*args):return ProgressPage(events=[],next_cursor=1)
 monkeypatch.setattr(module,'store',Store());monkeypatch.setattr(module,'engine_client',Engine())
 import pytest
 with pytest.raises(RuntimeError,match='empty progress'):asyncio.run(module._drain_engine_progress('t','s'))


def test_empty_poll_still_refuses_foreign_or_revoked_task(pg_workspace_factory,monkeypatch):
 import pytest
 from delivery_api import app as module
 from delivery_api.tenancy import tenant_context
 with pg_workspace_factory() as a,pg_workspace_factory() as b:
  with tenant_context(a.scope):t=a.store.create_task('Private empty poll')
  class Engine:
   def progress(self,*args):raise AssertionError('foreign/revoked read reached source')
  monkeypatch.setattr(module,'engine_client',Engine());monkeypatch.setattr(module,'store',a.store)
  with tenant_context(b.scope),pytest.raises(KeyError):asyncio.run(module._drain_engine_progress(t['task_id'],str(uuid4())))
  with a.admin() as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(a.scope.session_id,))
  with tenant_context(a.scope),pytest.raises(KeyError):asyncio.run(module._drain_engine_progress(t['task_id'],str(uuid4())))
