"""Recorded provider facts survive final settlement and exact owned export joins."""
import json,base64
import httpx,pytest
from fastapi.testclient import TestClient
from delivery_api.answer_runtime import AnswerRun,AnswerReceiptStore,AnswerProviderBroker
from delivery_api.answer_pricing import AnswerPricing
from delivery_api.tenancy import tenant_context
from test_answer_accounting_db import answer_workspace,balance
from test_child_provider_broker import BODY,request
from test_call_metadata import quote


def test_future_call_records_identity_usage_and_frozen_cost_in_same_two_batches(answer_workspace,pg_task_app,monkeypatch):
 w,job=answer_workspace;checkouts=[]
 original=w.store._postgres._checkout
 def checkout():
  checkouts.append(1);return original()
 monkeypatch.setattr(w.store._postgres,'_checkout',checkout)
 q=quote();q['model']='deepseek-flash'
 # Use a valid snapshot with the same reviewed route, not an invented current price.
 from delivery_api.provider_snapshot import ProviderSnapshot
 snapshot=q['provider_evidence']['snapshot'];snapshot['model']='deepseek-flash'
 q['provider_evidence']=ProviderSnapshot(snapshot).quote_evidence('2026-11-14T10:00:00Z')
 sent=[]
 with tenant_context(w.scope):
  run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='future',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
  with httpx.Client(transport=httpx.MockTransport(lambda r:(sent.append(r.content) or httpx.Response(200,headers={'content-type':'application/json'},content=BODY))),trust_env=False) as client:
   broker=AnswerProviderBroker(run,provider='deepseek',model='deepseek-flash',client=client,endpoint='https://fixed.invalid/v1/chat/completions',headers={},resolve_quote=lambda **kw:(q,'unused'))
   value=request()|{'metadata':{'item':'scenario:1','fields':['trigger','flow']}}
   assert broker.dispatch(value)==broker.dispatch(value)
  run.record_compute('0');settled=run.finish();assert run.finish()==settled
  tid=w.store.worker_job(job)['task_id']
 assert len(sent)==1 and len(checkouts)==3  # start, finish, explicit worker_job read
 client=TestClient(pg_task_app(w),headers=w.headers)
 row=client.get('/api/tasks/'+tid+'/cost-evidence?source=answer_usage').json()['rows'][0]
 assert row['logical_call_id']=='call-1' and row['fields']==['trigger','flow'] and row['item']=='scenario:1'
 assert row['provider']=='deepseek' and row['model']=='deepseek-flash' and row['latency_s']>=0
 assert row['input_tokens']==2 and row['output_tokens']==3 and row['cache_miss_tokens']==2
 assert row['provider_expense']['status']=='calculated_frozen_rates'
 assert row['provider_expense']['provider_expense_usd'] is not None and row['actual_retail_credits']=='0.0000625'
 assert row['job_id']==job and row['answer_id']=='future' and row['contribution_key'].startswith('answer-token:')
 assert balance(w)[0]==999937500
 with w.admin() as c:
  stored=c.execute('SELECT evidence FROM answer_charge_usage WHERE attempt_id=%s',(row['attempt_id'],)).fetchone()[0]
 assert stored['call_evidence']['logical_call_id']=='call-1'
 assert 'messages' not in json.dumps(stored) and 'serialized_request' not in json.dumps(stored)


def test_historical_calls_remain_unknown_without_backfill(answer_workspace,pg_task_app):
 from delivery_api.task_cost_export import page
 w,job=answer_workspace
 with tenant_context(w.scope):
  run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='old',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
  run.record_usage('old-attempt','intent',json.loads(BODY)['usage']);run.record_compute('0');run.finish()
  tid=w.store.worker_job(job)['task_id']
 row=TestClient(pg_task_app(w),headers=w.headers).get('/api/tasks/'+tid+'/cost-evidence?source=answer_usage').json()['rows'][0]
 assert row['logical_call_id'] is None and row['provider_expense'] is None


def test_migration_preserves_legacy_money_function_except_optional_evidence():
 from pathlib import Path
 root=Path(__file__).parents[1]/'supabase/migrations'
 old=(root/'20261111070000_answer_charge_receipts.sql').read_text()
 new=(root/'20261114051000_future_call_evidence.sql').read_text()
 start='FUNCTION public.requi_answer_finish('
 old=old[old.index(start):];old=old[:old.index('END $$;')+len('END $$;')]
 new=new[new.index(start):];new=new[:new.index('END $$;')+len('END $$;')]
 new=new.replace("item-ARRAY['attempt_id','phase','input_tokens','output_tokens','call_evidence']", "item-ARRAY['attempt_id','phase','input_tokens','output_tokens']")
 first=new.index("   IF item ? 'call_evidence'");last=new.index("   n:=(item->>'input_tokens')",first)
 new=new[:first]+new[last:]
 assert old==new  # No debit, rate, rounding, posting, or replay change.


def test_evidence_validation_failure_rolls_back_correction_and_records(answer_workspace):
 w,job=answer_workspace
 with tenant_context(w.scope):
  run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='invalid',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
  run.record_usage('invalid-attempt','intent',json.loads(BODY)['usage'],call_evidence={'version':'wrong'})
  run.record_compute('0')
  with pytest.raises(Exception,match='bounded call evidence'):run.finish()
 assert balance(w)[0]==900000000
 with w.admin() as c:
  assert c.execute('SELECT state,actual_units FROM answer_charge_receipts WHERE answer_id=%s',('invalid',)).fetchone()==('pending',None)
  assert c.execute('SELECT count(*) FROM answer_charge_usage WHERE answer_id=%s',('invalid',)).fetchone()[0]==0


def test_missing_capture_migration_is_detected_before_runtime_activation(answer_workspace):
 from delivery_api.answer_mode import check_schema
 w,_=answer_workspace
 with w.admin() as c:
  check_schema(c)
  with c.transaction(force_rollback=True):
   c.execute('DROP FUNCTION public.requi_call_evidence_version()')
   with pytest.raises(ValueError,match='migration missing'):check_schema(c)


def test_actual_reviewed_catalog_supplies_frozen_expense_without_admission_io(answer_workspace):
 from delivery_api.production_gateway import ProductionGateways
 from test_production_gateway import env,MODEL
 w,job=answer_workspace
 factory=ProductionGateways(w.store,env(),balance_transport=httpx.MockTransport(lambda _:pytest.fail('no balance/provider admission for diagnostic quoting')),vendor_fetcher=lambda _:pytest.fail('no new vendor fetch'))
 try:
  gateway=factory(None,job)
  with tenant_context(w.scope):
   run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='catalog',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
   with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={'content-type':'application/json'},content=BODY)),trust_env=False) as client:
    broker=AnswerProviderBroker(run,provider='deepseek',model=MODEL,client=client,endpoint='https://fixed.invalid/v1/chat/completions',headers={},resolve_quote=gateway.resolve_serialized)
    from test_child_provider_broker import RAW
    value=request()|{'model':MODEL,'serialized_request':base64.b64encode(json.dumps(json.loads(RAW)|{'model':MODEL}).encode()).decode()}
    assert broker.dispatch(value)['status']==200
   run.record_compute('0');run.finish()
   row=w.store.task_cost_export(w.store.worker_job(job)['task_id'],'answer_usage')['rows'][0]
  assert row['provider_expense']['status']=='calculated_frozen_rates'
  assert row['provider_expense']['provider_expense_usd']=='8E-7'
  assert row['model']==MODEL
 finally:factory.close()
