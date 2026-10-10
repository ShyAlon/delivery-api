"""Credit pricing from measured engine usage and process time, with the customer USD price (credits x USD_PER_CREDIT). Raw model cost_usd is kept in the payload but not displayed."""
from __future__ import annotations
import json,os
from decimal import Decimal
from .retail_pricing import RetailRates
from pathlib import Path
from .tenant_files import read_text as read_bound_text

_DEFAULT_RATES=RetailRates.from_environment(os.environ)
PRICE_PER_TOKEN=_DEFAULT_RATES.price_per_token
PRICE_PER_10MS=_DEFAULT_RATES.price_per_10ms
USD_PER_CREDIT=_DEFAULT_RATES.usd_per_credit
def retail_rates()->RetailRates:
    """Freeze these rates for a contribution; globals retain existing API compatibility."""
    return RetailRates(PRICE_PER_TOKEN,PRICE_PER_10MS,USD_PER_CREDIT)

_STAGE_PHASE={"probing":"intent","distillation":"intent","phrasing":"intent","scope_tiebreak":"intent","expert_opinion":"intent","conops":"conops","needs":"needs","srs":"srs","architecture":"architecture","items":"specs","tests":"tests","validation":"validation","review":"tests"}

def _rows(path:Path,start:int=0)->list[dict]:
    if not path.is_file():return []
    out=[]
    for line in read_bound_text(path,errors="replace").splitlines()[start:]:
        try:
            value=json.loads(line)
            if isinstance(value,dict):out.append(value)
        except json.JSONDecodeError:pass
    return out

def line_count(path:Path)->int:
    return len(read_bound_text(path,errors="replace").splitlines()) if path.is_file() else 0

def price_execution(usage_path:Path,llm_path:Path,wall_ms:float,usage_start:int=0,llm_start:int=0)->list[dict]:
    usage=_rows(usage_path,usage_start);calls=_rows(llm_path,llm_start)
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


def usage_tokens(path:Path)->int:
    """Prompt plus completion tokens recorded in one usage.jsonl."""
    return sum(int(r.get("prompt_tokens") or 0)+int(r.get("completion_tokens") or 0) for r in _rows(path))

def token_credits(tokens:int|float)->float:
    return float(retail_rates().credits(tokens))

def credits_to_usd(credits:float)->float:
    return float(Decimal(str(credits))*retail_rates().usd_per_credit)

def write_cost_json(session_root:Path)->dict|None:
    """Internal per-session cost rollup (contracts cost_rollup over the interview and
    downstream usage files). It sits beside the phase folders, is not a phase
    artifact, and is never listed, zipped or handed off. Estimate: tokens x the
    versioned price table, an upper bound (no cache-hit or off-peak pricing)."""
    from vmodel_contracts import cost_rollup
    rows=[]
    for area in ("interview","spec"):rows+=_rows(session_root/area/"usage.jsonl")
    if not rows:return None
    report=cost_rollup(rows)
    target=session_root/"cost.json";tmp=session_root/".cost.json.tmp"
    tmp.write_text(json.dumps(report,indent=1,sort_keys=True));tmp.replace(target)
    return report
