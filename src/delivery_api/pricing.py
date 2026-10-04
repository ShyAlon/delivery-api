"""Transparent credit pricing from measured engine usage and process time. A credit is the customer-facing price unit (about $1), covering model cost and overhead."""
from __future__ import annotations
import json,os
from pathlib import Path

PRICE_PER_TOKEN=float(os.environ.get("PRICE_PER_TOKEN","0.0000125"))
PRICE_PER_10MS=float(os.environ.get("PRICE_PER_10MS","0.0000005"))
_STAGE_PHASE={"probing":"intent","distillation":"intent","phrasing":"intent","scope_tiebreak":"intent","expert_opinion":"intent","conops":"conops","needs":"needs","srs":"srs","architecture":"architecture","items":"specs","tests":"tests","review":"tests"}

def _rows(path:Path,start:int=0)->list[dict]:
    if not path.is_file():return []
    out=[]
    for line in path.read_text(errors="replace").splitlines()[start:]:
        try:
            value=json.loads(line)
            if isinstance(value,dict):out.append(value)
        except json.JSONDecodeError:pass
    return out

def line_count(path:Path)->int:
    return len(path.read_text(errors="replace").splitlines()) if path.is_file() else 0

def price_execution(usage_path:Path,llm_path:Path,wall_ms:float,usage_start:int=0,llm_start:int=0)->list[dict]:
    usage=_rows(usage_path,usage_start);calls=_rows(llm_path,llm_start)
    llm_ms=sum(float(x.get("latency_s") or 0)*1000 for x in calls if x.get("event")=="llm_call_end")
    deterministic_ms=max(0.0,wall_ms-llm_ms)
    buckets={}
    for row in usage:
        phase=_STAGE_PHASE.get(str(row.get("stage")))
        if not phase:continue
        item=buckets.setdefault(phase,{"input_tokens":0,"output_tokens":0,"calls":0})
        item["input_tokens"]+=int(row.get("prompt_tokens") or 0);item["output_tokens"]+=int(row.get("completion_tokens") or 0);item["calls"]+=1
    total_calls=sum(x["calls"] for x in buckets.values()) or 1
    result=[]
    for phase,item in buckets.items():
        share=deterministic_ms*item["calls"]/total_calls;tokens=item["input_tokens"]+item["output_tokens"]
        token_price=tokens*PRICE_PER_TOKEN;infra_price=(share/10)*PRICE_PER_10MS
        result.append({"phase":phase,**item,"tokens":tokens,"deterministic_ms":round(share,3),"token_price":round(token_price,6),"infra_price":round(infra_price,6),"price":round(token_price+infra_price,6),"rates":{"price_per_token":PRICE_PER_TOKEN,"price_per_10ms":PRICE_PER_10MS,"scale":"credits"}})
    return result
