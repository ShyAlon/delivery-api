import asyncio,logging
from types import SimpleNamespace
from starlette.requests import Request
from starlette.responses import Response
import pytest
from delivery_api import app as m
from delivery_api import diagnostic_evidence as d


def request(method='GET',path='/api/tasks/task-a'):
 return Request({'type':'http','method':method,'path':path,'raw_path':path.encode(),'query_string':b'','headers':[], 'server':('test',80),'client':('test',1),'scheme':'http'})


@pytest.mark.parametrize('kind',['control','operator','csrf','normal'])
def test_every_refusal_and_completed_request_has_durable_scope(monkeypatch,tmp_path,kind):
 monkeypatch.setattr(m,'control_plane_only',lambda:kind=='control')
 monkeypatch.setattr(m.config,'CLOUD_TENANTS',False)
 def csrf(r):
  if kind=='csrf':raise m.HTTPException(status_code=403,detail='refused')
 monkeypatch.setattr(m.auth,'csrf_guard',csrf)
 monkeypatch.setattr(m.auth,'actor',lambda r:SimpleNamespace(label='test'))
 path='/api/operator/task-debug' if kind=='operator' else '/api/tasks/task-a'
 req=request('POST' if kind=='control' else 'GET',path)
 async def next_(r):return Response(status_code=400)
 p=tmp_path/'evidence';h=d.EvidenceHandler(p);root=logging.getLogger();root.addHandler(h);m.activity.__globals__['logger'].addHandler(h)
 try:response=asyncio.run(m.request_log(req,next_))
 finally:root.removeHandler(h);m.activity.__globals__['logger'].removeHandler(h);h.close()
 assert response.status_code in (400,403,404,503)
 ref=response.headers.get('X-Request-Id');assert ref
 records=d.read(p)['text'];assert ref in records
 if kind!='operator':
  import json
  assert any(json.loads(line).get('task_id')=='task-a' for line in records.splitlines())
 assert d.context.get()=={}
