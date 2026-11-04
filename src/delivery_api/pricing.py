"""Credit pricing from measured engine usage and process time, with the customer USD price (credits x USD_PER_CREDIT). Raw model cost_usd is kept in the payload but not displayed."""
from __future__ import annotations
import os
from decimal import Decimal
from .retail_pricing import RetailRates

_DEFAULT_RATES=RetailRates.from_environment(os.environ)
PRICE_PER_TOKEN=_DEFAULT_RATES.price_per_token
PRICE_PER_10MS=_DEFAULT_RATES.price_per_10ms
USD_PER_CREDIT=_DEFAULT_RATES.usd_per_credit
def retail_rates()->RetailRates:
    """Freeze these rates for a contribution; globals retain existing API compatibility."""
    return RetailRates(PRICE_PER_TOKEN,PRICE_PER_10MS,USD_PER_CREDIT)

_STAGE_PHASE={"probing":"intent","distillation":"intent","phrasing":"intent","scope_tiebreak":"intent","expert_opinion":"intent","conops":"conops","needs":"needs","srs":"srs","architecture":"architecture","items":"specs","tests":"tests","validation":"validation","review":"tests"}

def price_rows(usage:list[dict],calls:list[dict],wall_ms:float)->list[dict]:
    """Pure measured-evidence calculator. No path readers or durable writes."""
    llm_ms=sum(float(x.get("latency_s") or 0)*1000 for x in calls if x.get("event")=="llm_call_end")
    deterministic_ms=max(0.0,wall_ms-llm_ms)
    buckets={}
    for row in usage:
        phase=_STAGE_PHASE.get(str(row.get("stage")))
        if not phase:continue
        item=buckets.setdefault(phase,{"input_tokens":0,"output_tokens":0,"calls":0,"cost_usd":0.0,"unpriced_calls":0})
        item["input_tokens"]+=int(row.get("prompt_tokens") or 0);item["output_tokens"]+=int(row.get("completion_tokens") or 0);item["calls"]+=1
        pin=row.get("price_per_mtok_prompt_usd");pout=row.get("price_per_mtok_completion_usd")
        if pin is None or pout is None:item["unpriced_calls"]+=1
        else:item["cost_usd"]+=(int(row.get("prompt_tokens") or 0)*float(pin)+int(row.get("completion_tokens") or 0)*float(pout))/1_000_000
    total_calls=sum(x["calls"] for x in buckets.values()) or 1
    result=[]
    for phase,item in buckets.items():
        share=deterministic_ms*item["calls"]/total_calls;tokens=item["input_tokens"]+item["output_tokens"]
        token_exact,infra_exact=retail_rates().components(tokens,Decimal(str(share)))
        token_price=float(token_exact);infra_price=float(infra_exact)
        result.append({"phase":phase,**item,"tokens":tokens,"deterministic_ms":round(share,3),"token_price":round(token_price,6),"infra_price":round(infra_price,6),"price":float(round(token_exact+infra_exact,6)),"price_usd":float(round((token_exact+infra_exact)*retail_rates().usd_per_credit,6)),"cost_usd":round(item["cost_usd"],8),"unpriced_calls":item["unpriced_calls"],"rates":{"price_per_token":float(PRICE_PER_TOKEN),"price_per_10ms":float(PRICE_PER_10MS),"usd_per_credit":float(USD_PER_CREDIT),"scale":"credits"}})
    return result


def token_credits(tokens:int|float)->float:
    return float(retail_rates().credits(tokens))

def credits_to_usd(credits:float)->float:
    return float(Decimal(str(credits))*retail_rates().usd_per_credit)
