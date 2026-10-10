"""Reviewed catalog and complete-request bound registry. No production defaults.

Adapters must see the complete provider payload, including tool/system fields.
An approximate tokenizer or messages-only estimate cannot claim a verified bound.
"""
from dataclasses import dataclass
import hashlib,json
from .attempt_ledger import AdmissionDenied,_integer

@dataclass(frozen=True)
class ReviewedModelPrice:
    provider:str
    model:str
    price_version:str
    source:str
    expense_input:int
    expense_output:int
    retail_input:int
    retail_output:int
    usage_categories:tuple[str,...]=('input_tokens','output_tokens')
    retail_units_per_credit:int|None=None
    expense_cache_hit:int|None=None
    expense_cache_miss:int|None=None
    expense_units_per_usd:int|None=None
    retail_formula:dict|None=None
    provider_snapshot:dict|None=None

@dataclass(frozen=True)
class RequestBound:
    bound_version:str
    input_max:int
    output_max:int
    serialized_request:bytes
    includes_complete_request:bool
    estimate_policy:dict|None=None

class ReviewedCatalog:
    def __init__(self,prices=(),bounders=None):
        self.prices={};self.bounders=bounders or {}
        for price in prices:
            if not all(isinstance(value,str) and value for value in (price.provider,price.model,price.price_version,price.source)) or any(not _integer(getattr(price,k)) for k in ('expense_input','expense_output','retail_input','retail_output')):
                raise AdmissionDenied('reviewed source/version and fixed-point prices required')
            key=(price.provider,price.model)
            if key in self.prices:raise AdmissionDenied('duplicate reviewed model price')
            # Other accounting needs an explicit extension, not a guessed fold-in.
            if price.usage_categories not in [('input_tokens','output_tokens'),('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens')]:
                raise AdmissionDenied('unsupported accounting categories')
            cache=price.usage_categories==('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens')
            if cache and not all(_integer(n) for n in (price.expense_cache_hit,price.expense_cache_miss)):
                raise AdmissionDenied('explicit reviewed cache prices required')
            if not cache and (price.expense_cache_hit is not None or price.expense_cache_miss is not None):
                raise AdmissionDenied('cache prices need cache accounting categories')
            if price.retail_units_per_credit is not None and (not _integer(price.retail_units_per_credit) or not price.retail_units_per_credit):
                raise AdmissionDenied('explicit positive retail unit scale required')
            if price.expense_units_per_usd is not None and (not _integer(price.expense_units_per_usd) or not price.expense_units_per_usd):raise AdmissionDenied('positive expense unit scale required')
            self.prices[key]=price

    def resolve(self,*,stage,provider,model,messages,max_output_tokens):
        key=(provider,model);price=self.prices.get(key);bounder=self.bounders.get(key)
        if not price or not bounder:raise AdmissionDenied('model price or complete-request bound is unreviewed')
        bound=bounder(stage=stage,messages=messages,max_output_tokens=max_output_tokens)
        if not isinstance(bound,RequestBound) or not bound.includes_complete_request or not bound.bound_version or not isinstance(bound.serialized_request,bytes) or not bound.serialized_request:
            raise AdmissionDenied('verified complete provider request required')
        if not _integer(bound.input_max) or not _integer(bound.output_max) or not bound.output_max or bound.output_max!=max_output_tokens:
            raise AdmissionDenied('request output bound differs from provider cap')
        quote={k:getattr(price,k) for k in ('provider','model','price_version','expense_input','expense_output','retail_input','retail_output')}
        if price.expense_cache_hit is not None:quote.update(expense_cache_hit=price.expense_cache_hit,expense_cache_miss=price.expense_cache_miss)
        if price.retail_units_per_credit is not None:quote['retail_units_per_credit']=price.retail_units_per_credit
        if price.expense_units_per_usd is not None:quote['expense_units_per_usd']=price.expense_units_per_usd
        if price.retail_formula is not None:
            from .pricing import _STAGE_PHASE
            quote['retail_formula']=dict(price.retail_formula)
            quote['retail_phase']=_STAGE_PHASE.get(stage,stage)
        if price.provider_snapshot is not None:
            from .provider_snapshot import ProviderSnapshot
            from datetime import datetime,timezone
            snapshot=ProviderSnapshot(price.provider_snapshot)
            quote['provider_evidence']=snapshot.quote_evidence(datetime.now(timezone.utc))
        if bound.estimate_policy is not None:quote['estimate_policy']=dict(bound.estimate_policy)
        quote.update(bound_version=bound.bound_version,input_max=bound.input_max,output_max=bound.output_max)
        digest=hashlib.sha256(bound.serialized_request).hexdigest()
        return quote,digest

    def resolve_serialized(self,*,stage,provider,model,serialized_request,max_output_tokens):
        if not isinstance(serialized_request,bytes) or not serialized_request:
            raise AdmissionDenied('serialized SDK request required')
        # Serialized adapters are distinct from legacy messages-only adapters.
        adapter=self.bounders.get(('serialized',provider,model))
        if adapter is None:raise AdmissionDenied('serialized request bound is unreviewed')
        bound=adapter(stage=stage,serialized_request=serialized_request,max_output_tokens=max_output_tokens)
        if not isinstance(bound,RequestBound) or bound.serialized_request!=serialized_request:
            raise AdmissionDenied('bound must cover exact SDK bytes')
        pinned=ReviewedCatalog(self.prices.values(),{(provider,model):lambda **kw:bound})
        return pinned.resolve(stage=stage,provider=provider,model=model,messages=None,max_output_tokens=max_output_tokens)

    @staticmethod
    def measure_standard_usage(response):
        usage=getattr(response,'usage_metadata',None)
        # Extra categories must not be priced as zero or silently discarded.
        if not isinstance(usage,dict) or set(usage)-{'input_tokens','output_tokens','total_tokens'}:
            return None
        if not all(_integer(usage.get(k)) for k in ('input_tokens','output_tokens')):return None
        if 'total_tokens' in usage and usage['total_tokens']!=usage['input_tokens']+usage['output_tokens']:return None
        return {k:usage[k] for k in ('input_tokens','output_tokens')}


def catalog_from_environment(environ, *, bounders=None):
    """Explicit reviewed fixed-point config, no default model or monetary value.

    Snapshot source/version/rates into frozen catalog entries. Updating environment
    values cannot reprice existing ledger quotes. This does not enable execution.
    """
    raw=environ.get('VMODEL_REVIEWED_MODEL_PRICES')
    if not raw:raise AdmissionDenied('reviewed environment model prices missing')
    try:
        rows=json.loads(raw)
        if not isinstance(rows,list) or not rows:raise ValueError()
        prices=[]
        required={'provider','model','price_version','source','expense_input','expense_output','retail_input','retail_output'}
        for row in rows:
            allowed=required|{'expense_units_per_usd','retail_units_per_credit','usage_categories','expense_cache_hit','expense_cache_miss','retail_formula','provider_snapshot'}
            if not isinstance(row,dict) or not required<=set(row) or set(row)-allowed:raise ValueError()
            row=dict(row)
            if 'usage_categories' in row:
                if not isinstance(row['usage_categories'],list) or not all(isinstance(x,str) for x in row['usage_categories']):raise ValueError()
                row['usage_categories']=tuple(row['usage_categories'])
            prices.append(ReviewedModelPrice(**row))
        return ReviewedCatalog(prices,bounders)
    except (TypeError,ValueError) as exc:
        raise AdmissionDenied('invalid reviewed environment model prices') from exc


@dataclass(frozen=True)
class ReviewedContextCeiling:
    """Conservative model-wide ceiling, not approximate per-message counting.

    Caller supplies reviewed numeric context/output limits with source/version.
    This covers hidden tool serialization by reserving the entire accepted
    context, and may reject otherwise affordable calls due to overreservation.
    No production models or limits are registered by this class.
    """
    model:str
    context_tokens:int
    maximum_output_tokens:int
    source:str
    bound_version:str
    protective_run_ceiling:bool=False

    def __post_init__(self):
        if not all(isinstance(x,str) and x for x in (self.model,self.source,self.bound_version)) or not _integer(self.context_tokens) or not self.context_tokens or not _integer(self.maximum_output_tokens) or not self.maximum_output_tokens:
            raise AdmissionDenied('reviewed context ceiling source/version/limits required')

    def __call__(self,*,stage,serialized_request,max_output_tokens):
        if not isinstance(serialized_request,bytes) or not serialized_request:raise AdmissionDenied('complete SDK bytes required')
        try:
            payload=json.loads(serialized_request)
            caps=[payload[k] for k in ('max_tokens','max_completion_tokens') if k in payload]
            if not isinstance(payload,dict) or payload.get('model')!=self.model or len(caps)!=1 or type(caps[0]) is not int or caps[0]!=max_output_tokens:raise ValueError()
        except (ValueError,TypeError,AttributeError) as exc:raise AdmissionDenied('context ceiling request identity/cap mismatch') from exc
        if not _integer(max_output_tokens) or not 0<max_output_tokens<=min(self.maximum_output_tokens,self.context_tokens):
            raise AdmissionDenied('provider output cap exceeds reviewed ceiling')
        if self.protective_run_ceiling:
            # Deliberately conservative estimate, NOT a tokenizer proof.
            # Count the entire wire payload one unit/byte, plus framing headroom.
            estimate=len(serialized_request)+4096
            if estimate+max_output_tokens>self.context_tokens:
                raise AdmissionDenied('request estimate plus output cap exceeds owner policy ceiling')
            policy={'kind':'complete-wire-bytes-plus4096-v1','ceiling':self.context_tokens,'input_estimate':estimate,'hard_output_cap':max_output_tokens,'verified_input_bound':False}
            return RequestBound(self.bound_version,estimate,max_output_tokens,serialized_request,True,policy)
        # Do not subtract output cap: max output is not guaranteed to be used.
        return RequestBound(self.bound_version,self.context_tokens,max_output_tokens,serialized_request,True)
