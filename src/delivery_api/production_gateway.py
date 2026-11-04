"""Builds the per-job money gateway for cloud runs from reviewed server configuration.

This is the service-owned version of what the item6 harness assembled by hand. Nothing here has a default: the reviewed
price row (with retail formula and vendor snapshot), the reviewed context ceiling and the provider key must all be
present and consistent, or building raises AdmissionDenied and no run can start. Building does not call the provider,
open a database connection or change any state. Vendor pages are read only when a call is about to be admitted."""
import json

import httpx

from . import vendor_status
from .admission_catalog import ReviewedContextCeiling,catalog_from_environment
from .attempt_ledger import AdmissionDenied
from .deepseek_usage import settlement_usage as deepseek_settlement_usage
from .ollama_usage import settlement_usage as ollama_settlement_usage
from .launch_admission_policy import validate_launch_quote
from .producer_admission import WorkerAdmissionGateway
from .provider_network import FixedOriginTransport,deepseek_balance_reader
from .retail_ledger import rates_from_snapshot
from . import vendor_sources

PRICES_ENV='VMODEL_REVIEWED_MODEL_PRICES'
CEILING_ENV='VMODEL_REVIEWED_CONTEXT_CEILING'
KEY_ENV='DEEPSEEK_API_KEY'
PREPAID_UNITS=10**9


class ProductionGateways:
    """Callable gateway factory: factory(capability,job_id) returns a WorkerAdmissionGateway for that job."""

    def __init__(self,store,environ,*,balance_transport=None,vendor_fetcher=None):
        try:
            rows=json.loads(environ.get(PRICES_ENV) or 'null')
            context=json.loads(environ.get(CEILING_ENV) or 'null')
        except ValueError as exc:
            raise AdmissionDenied('reviewed prices or context ceiling are not valid JSON') from exc
        if isinstance(rows,list) and len(rows)==2:
            self._init_dual(store,environ,rows,context,balance_transport=balance_transport,vendor_fetcher=vendor_fetcher)
            return
        if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
            raise AdmissionDenied('exactly one reviewed model price row required')
        row=rows[0]
        if not isinstance(context,dict):raise AdmissionDenied('reviewed context ceiling required')
        if row.get('provider')=='ollama':
            self._init_local_ollama(store,environ,row,context)
            self.models={'ollama':self.model}
            return
        api_key=(environ.get(KEY_ENV) or '').strip()
        if not api_key:raise AdmissionDenied('provider key required')
        if row.get('provider')!='deepseek':raise AdmissionDenied('launch provider must be DeepSeek')
        if row.get('retail_units_per_credit')!=PREPAID_UNITS:raise AdmissionDenied('exact 1000000000-unit prepaid scale required')
        if 'retail_formula' not in row or 'provider_snapshot' not in row:raise AdmissionDenied('retail formula and vendor snapshot required')
        try:ceiling=ReviewedContextCeiling(**context)
        except TypeError as exc:raise AdmissionDenied('reviewed context ceiling fields invalid') from exc
        model=row.get('model')
        if ceiling.model!=model:raise AdmissionDenied('context ceiling must name the reviewed model')
        catalog=catalog_from_environment({PRICES_ENV:json.dumps(rows)},bounders={('serialized','deepseek',model):ceiling})
        quote,_=catalog.resolve_serialized(stage='preflight',provider='deepseek',model=model,
            serialized_request=json.dumps({'model':model,'max_tokens':1}).encode(),max_output_tokens=1)
        validate_launch_quote(quote)
        self.models={'deepseek':model}
        self.local=False
        self.model=model
        self.store=store
        self.catalog=catalog
        self.retail_rates=rates_from_snapshot(row['retail_formula'])
        snapshot=row['provider_snapshot']
        vendor_status.pin(snapshot)
        fetcher=vendor_fetcher or vendor_sources.fetch
        self.verify_vendor=lambda:vendor_sources.verify_current_sources(snapshot,fetcher)
        self.balance_client=httpx.Client(transport=FixedOriginTransport(balance_transport or httpx.HTTPTransport(retries=0),
            'https://api.deepseek.com',lambda method,path:method=='GET' and path=='/user/balance'),trust_env=False,follow_redirects=False)
        self.provider_balance=deepseek_balance_reader(client=self.balance_client,api_key=api_key)

    def __call__(self,capability,job_id):
        if hasattr(self,'_providers'):
            job=self.store.worker_job(job_id) or {}
            selected=(job.get('execution_options') or {}).get('model_route')
            if not selected:raise AdmissionDenied('durable provider:model selection required for dual-provider job')
            provider,sep,model=selected.partition(':')
            if not sep or self.models.get(provider)!=model:raise AdmissionDenied('durable provider:model selection is not reviewed')
            child=self._providers[provider](capability,job_id)
            child.bound_model=(provider,model)
            return child
        if self.local:
            return WorkerAdmissionGateway(self.store,job_id,resolve_serialized=self.catalog.resolve_serialized,
                measure_raw_usage=ollama_settlement_usage,require_prepaid=True)
        return WorkerAdmissionGateway(self.store,job_id,resolve_serialized=self.catalog.resolve_serialized,
            measure_raw_usage=deepseek_settlement_usage,require_prepaid=True,retail_rates=self.retail_rates,
            verify_provider_snapshot=self.verify_vendor,provider_balance=self.provider_balance,launch_guard=True)

    def _init_dual(self,store,environ,rows,contexts,**kw):
        """Two independently reviewed providers, never one mixed accounting adapter.

        Bind to the immutable job choice before compute metering starts. Each child
        enforces that same choice at reserve and owns settlement/recovery unchanged.
        """
        providers={r.get('provider') for r in rows if isinstance(r,dict)}
        if providers!={'deepseek','ollama'}:raise AdmissionDenied('one reviewed row per supported provider required')
        if not isinstance(contexts,list) or len(contexts)!=2 or any(not isinstance(c,dict) for c in contexts):
            raise AdmissionDenied('one provider-keyed reviewed context per provider required')
        by_provider={c.get('provider'):c for c in contexts}
        if set(by_provider)!=providers:raise AdmissionDenied('reviewed provider contexts must match price rows exactly')
        self._providers={};self.store=store
        try:
            for row in rows:
                provider=row['provider'];context=dict(by_provider[provider]);context.pop('provider')
                child_env=dict(environ)
                child_env[PRICES_ENV]=json.dumps([row]);child_env[CEILING_ENV]=json.dumps(context)
                # Key belongs only to the paid child. It is never passed into the
                # local child's configuration, checks, or transport.
                if provider=='ollama':child_env.pop(KEY_ENV,None)
                self._providers[provider]=ProductionGateways(store,child_env,**kw)
        except BaseException:
            for child in self._providers.values():child.close()
            raise
        self.models={provider:child.model for provider,child in self._providers.items()}
        self.verify_vendor=self._providers['deepseek'].verify_vendor

    def _init_local_ollama(self,store,environ,row,context):
        """The operator's local execution preview: one reviewed $0 local Ollama row.

        No provider key, vendor snapshot, retail formula or balance read exists for a
        local model. The catalog still requires the reviewed row and context ceiling,
        and every call reserves/settles through the same shared ledger at $0.
        """
        if (environ.get(KEY_ENV) or '').strip():raise AdmissionDenied('the local Ollama route carries no provider key')
        if 'retail_formula' in row or 'provider_snapshot' in row:raise AdmissionDenied('the local route carries no retail formula or vendor snapshot')
        if row.get('retail_units_per_credit')!=PREPAID_UNITS:raise AdmissionDenied('exact 1000000000-unit prepaid scale required')
        if any(row.get(k)!=0 for k in ('expense_input','expense_output','retail_input','retail_output')):raise AdmissionDenied('the local route prices must be zero')
        if row.get('expense_cache_hit') is not None or row.get('expense_cache_miss') is not None:raise AdmissionDenied('the local route has no cache billing')
        try:ceiling=ReviewedContextCeiling(**context)
        except TypeError as exc:raise AdmissionDenied('reviewed context ceiling fields invalid') from exc
        model=row.get('model')
        if ceiling.model!=model:raise AdmissionDenied('context ceiling must name the reviewed model')
        if ceiling.protective_run_ceiling:raise AdmissionDenied('the local route reserves the reviewed window, not a byte estimate')
        catalog=catalog_from_environment({PRICES_ENV:json.dumps([row])},bounders={('serialized','ollama',model):ceiling})
        quote,_=catalog.resolve_serialized(stage='preflight',provider='ollama',model=model,
            serialized_request=json.dumps({'model':model,'max_tokens':1}).encode(),max_output_tokens=1)
        validate_local_launch_quote(quote)
        self.local=True
        self.model=model
        self.store=store
        self.catalog=catalog
        self.retail_rates=None
        self.verify_vendor=None
        self.balance_client=None
        self.provider_balance=None

    def close(self):
        if hasattr(self,'_providers'):
            for child in self._providers.values():child.close()
            return
        if self.balance_client is not None:self.balance_client.close()


def gateways_from_environment(store,environ,**kw):
    return ProductionGateways(store,environ,**kw)


def validate_local_launch_quote(quote):
    """The local launch check: a complete, exact, zero-priced Ollama quote with no estimate or vendor evidence."""
    from .attempt_ledger import _quote
    _quote(quote)
    if quote.get('provider')!='ollama':raise AdmissionDenied('local launch provider must be Ollama')
    if any(quote[k] for k in ('expense_input','expense_output','retail_input','retail_output')):raise AdmissionDenied('local launch quote must be zero-priced')
    if 'estimate_policy' in quote or 'provider_evidence' in quote or 'retail_formula' in quote:raise AdmissionDenied('local launch quote carries no estimate, vendor evidence or retail formula')
    return dict(quote)
