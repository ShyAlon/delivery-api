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
from .deepseek_usage import settlement_usage
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
        if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
            raise AdmissionDenied('exactly one reviewed model price row required')
        row=rows[0]
        if not isinstance(context,dict):raise AdmissionDenied('reviewed context ceiling required')
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
        return WorkerAdmissionGateway(self.store,job_id,resolve_serialized=self.catalog.resolve_serialized,
            measure_raw_usage=settlement_usage,require_prepaid=True,retail_rates=self.retail_rates,
            verify_provider_snapshot=self.verify_vendor,provider_balance=self.provider_balance)

    def close(self):
        self.balance_client.close()


def gateways_from_environment(store,environ,**kw):
    return ProductionGateways(store,environ,**kw)
