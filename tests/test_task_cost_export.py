from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from delivery_api.tenancy import tenant_context


def test_all_cost_sources_are_owned_select_only_and_empty_is_truthful(pg_scoped_workspace,pg_task_app):
 w=pg_scoped_workspace
 with tenant_context(w.scope):tid=w.store.create_task('Cost export '+uuid4().hex)['task_id']
 client=TestClient(pg_task_app(w),headers=w.headers)
 for source in ('answers','answer_usage','provider_attempts','retail_contributions'):
  r=client.get('/api/tasks/'+tid+'/cost-evidence',params={'source':source})
  assert r.status_code==200,r.text
  data=r.json();assert data['rows']==[] and data['complete'] and data['next_cursor'] is None
 assert client.get('/api/tasks/'+tid+'/cost-evidence?source=evil').status_code==422
 assert client.get('/api/tasks/missing/cost-evidence').status_code==404


def test_many_recorded_contributions_page_without_twenty_row_tail(pg_scoped_workspace,pg_task_app,monkeypatch,pg_workspace_factory):
 from delivery_api import task_cost_export as export
 w=pg_scoped_workspace;monkeypatch.setattr(export,'PAGE_SIZE',17)
 with tenant_context(w.scope):
  tid=w.store.create_task('Full history '+uuid4().hex)['task_id'];w.store.set_engine_session(tid,str(uuid4()))
  job=w.store.enqueue_worker(tid,'phase:needs')
 # Test fixtures only, isolated local PG. No accounting mutation in the export.
 from delivery_api.retail_pricing import RetailRates
 import json
 rates=RetailRates('0.001','0.01','1').snapshot()
 with w.admin() as conn:
  for i in range(53):
   conn.execute('INSERT INTO retail_contributions VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s::jsonb,%s,%s,1000000000,%s,%s)',
    ('export-fixture:'+str(i).zfill(4),w.scope.owner_id,job,'needs','tokens',i,3,json.dumps(rates),i+3,i+3,'fixture-'+str(i),1000+i))
 client=TestClient(pg_task_app(w),headers=w.headers);rows=[];after=''
 for _ in range(10):
  reply=client.get('/api/tasks/'+tid+'/cost-evidence',params={'source':'retail_contributions','after':after});assert reply.status_code==200,reply.text
  data=reply.json();rows+=data['rows']
  if data['complete']:break
  after=data['next_cursor']
 assert len(rows)==53 and len({r['contribution_key'] for r in rows})==53
 assert [r['input_tokens'] for r in rows]==list(range(53))
 with pg_workspace_factory() as other:
  assert client.get('/api/tasks/'+tid+'/cost-evidence',headers=other.headers).status_code==404


def test_answer_usage_joins_exact_settlement_not_timestamp_guess(pg_scoped_workspace,pg_task_app):
 from test_answer_accounting_db import begin,finish,usage
 w=pg_scoped_workspace
 with w.admin() as c:
  c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1000000000,1000000000,0)',(w.scope.owner_id,))
 with tenant_context(w.scope):
  tid=w.store.create_task('Recorded answer cost '+uuid4().hex)['task_id'];w.store.set_engine_session(tid,str(uuid4()))
  job=w.store.enqueue_worker(tid,'phase:intent');w.store.claim_worker(job)
  begin(w,job,aid='export-answer');u=usage(tokens=123);u['attempts'][0]['attempt_id']='export-attempt-'+uuid4().hex;finish(w,aid='export-answer',u=u)
 client=TestClient(pg_task_app(w),headers=w.headers)
 row=client.get('/api/tasks/'+tid+'/cost-evidence?source=answer_usage').json()['rows'][0]
 assert row['answer_id']=='export-answer' and row['attempt_id']==u['attempts'][0]['attempt_id'] and row['input_tokens']==123
 assert row['contribution_key'].startswith('answer-token:') and row['posted_units'] is not None
 assert row['actual_retail_credits'] is not None
 assert row['logical_call_id'] is None and row['provider_expense'] is None and row['model'] is None
 with w.admin() as c:
  direct=c.execute('SELECT contribution_key,posted_units FROM retail_contributions WHERE job_id=%s AND kind=%s',(job,'tokens')).fetchone()
 assert (row['contribution_key'],row['posted_units'])==direct
