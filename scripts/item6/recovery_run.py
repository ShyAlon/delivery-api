"""Paid continuation after separately approved, reconciled isolated restoration.

No signup/purchase retry. Caller restores a copy and verifies exact source hashes.
"""
import json,os
from pathlib import Path
from delivery_api.tenancy import tenant_context
from delivery_api.deepseek_usage import settlement_usage
from delivery_api.producer_admission import WorkerAdmissionGateway
from delivery_api.admission_catalog import catalog_from_environment,ReviewedContextCeiling
from delivery_api.retail_ledger import rates_from_snapshot
from pipeline_executor import PrivatePipeline
from network_boundaries import deepseek_client_factory,deepseek_balance_reader,FixedOriginTransport
from vendor_config import verify_current_sources
from recovery_budget import RecoveryBudgetGateway
from recovery_continuation import execute_continuation


def run(*,store,scope,root,runtime,run_id,task_id,prior_job,output,maximum_expense_units,interrupted_job=None,pending_review=None):
    import httpx
    runtime=Path(runtime)
    prices=json.loads((runtime/'catalog.json').read_text());context=json.loads((runtime/'context.json').read_text())
    credentials=json.loads((runtime/'credentials.json').read_text())
    model=prices[0]['model']
    if any(p['model']!=model or p['provider']!='deepseek' for p in prices):raise ValueError('one reviewed DeepSeek model required')
    os.environ['DEEPSEEK_API_KEY']=credentials['deepseek_api_key']
    for key in list(os.environ):
        if key.startswith(('INTERVIEW_ENGINE_CATEGORIZER_','VMODEL_REPLAY','VMODEL_CAP_')) or key in ('VMODEL_LLM_MAX_TOKENS','VMODEL_TENANT_OWNER','INTERVIEW_ENGINE_TENANTS'):
            os.environ.pop(key,None)
    os.environ['INTERVIEW_ENGINE_CATEGORIZERS']='llm,rules';os.environ.pop('TYPESAFE_API_KEY',None)
    os.environ['VMODEL_PRICE_TABLE']=str(Path(root)/'diagnostic-prices.json')
    catalog=catalog_from_environment({'VMODEL_REVIEWED_MODEL_PRICES':json.dumps(prices)},bounders={('serialized','deepseek',model):ReviewedContextCeiling(**context)})
    rates=rates_from_snapshot(prices[0]['retail_formula'])
    with tenant_context(scope):
        with store._postgres.transaction() as c:
            original_jobs=[r['job_id'] for r in c.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s',(task_id,)).fetchall()]
        expected={prior_job} if interrupted_job is None else {'edad945ddadfa63b5bd73d5546a5bf69',interrupted_job}
        if set(original_jobs)!=expected:raise ValueError('exact audited predecessors required')
        budget_exempt=['edad945ddadfa63b5bd73d5546a5bf69']
    balance_client=httpx.Client(transport=FixedOriginTransport(httpx.HTTPTransport(retries=0),'https://api.deepseek.com',lambda method,path:method=='GET' and path=='/user/balance'),trust_env=False,follow_redirects=False)
    reader=deepseek_balance_reader(client=balance_client,api_key=credentials['deepseek_api_key'])
    def gateway(cap,job):
        inner=WorkerAdmissionGateway(store,job,resolve_serialized=catalog.resolve_serialized,measure_raw_usage=settlement_usage,
          require_prepaid=True,retail_rates=rates,verify_provider_snapshot=lambda:verify_current_sources(prices[0]['provider_snapshot']),provider_balance=reader)
        return RecoveryBudgetGateway(inner,original_job_ids=budget_exempt,maximum_expense_units=maximum_expense_units)
    def pipeline_factory(attended):
        return PrivatePipeline(store=store,artifacts_base=root,model=model,gateway_factory=gateway,
          generator_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='spec_generator'),
          interview_clients=deepseek_client_factory(inner_sync=lambda:httpx.HTTPTransport(retries=0),inner_async=lambda:httpx.AsyncHTTPTransport(retries=0),owner_module='interview_engine'),
          answer_input=attended.input,review_gate=attended.gate,progress=lambda state,text:print(state+' | '+text,flush=True))
    try:
        return execute_continuation(store=store,scope=scope,root=root,config={},run_id=run_id,task_id=task_id,prior_job=prior_job,
           output=output,pipeline_factory=pipeline_factory,original_failed_jobs=original_jobs,pending_review=pending_review)
    finally:balance_client.close()
