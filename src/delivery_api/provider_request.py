"""Exact serialized model/cap contract shared by parent admission boundaries."""
import json
from requi_child_contract import _unique_pairs,_invalid_constant


def request_contract(raw,model,output_cap=None):
    payload=json.loads(raw,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
    if not isinstance(payload,dict):raise ValueError('provider object required')
    caps=[payload[k] for k in ('max_tokens','max_completion_tokens') if k in payload]
    if (payload.get('model')!=model or len(caps)!=1 or type(caps[0]) is not int or caps[0]<=0
        or (output_cap is not None and (type(output_cap) is not int or caps[0]!=output_cap))):
        raise ValueError('exact model and output cap required')
    return payload,caps[0]
