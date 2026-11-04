import asyncio
from types import SimpleNamespace
import pytest
from delivery_api import diagnostic_evidence as d
from delivery_api.tenant_jobs import execute_job
from delivery_api.tenancy import TenantScope,tenant_context


def test_claimed_capability_is_the_diagnostic_binding(monkeypatch):
 cap=SimpleNamespace(task_id='task',session_id='session',validate=lambda store:None)
 seen=[]
 store=SimpleNamespace(claim_worker=lambda job:cap,task=lambda task:None,finish_worker=lambda *a,**k:None)
 async def handler(cap):seen.append(dict(d.context.get()))
 with d.bound(request_id='request'):
  with tenant_context(TenantScope('11111111-1111-1111-1111-111111111111')):
   assert asyncio.run(execute_job(store,'job',handler))
  assert d.context.get()=={'request_id':'request'}
 assert seen==[{'request_id':'request','task_id':'task','session_id':'session','job_id':'job'}]
