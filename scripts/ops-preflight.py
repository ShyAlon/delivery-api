#!/usr/bin/env python3
"""Print only offline check booleans. Never dump environment values or connect."""
import json,os
from delivery_api.ops_preflight import preflight
if __name__=='__main__':
    result=preflight(os.environ)
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result['configuration_valid'] else 1)
