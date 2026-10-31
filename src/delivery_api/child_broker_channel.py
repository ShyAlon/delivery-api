"""Private parent provider channel, opt-in constructor seam only."""
import json
from .child_channel import json_channel


def provider_broker_channel(broker):
    return json_channel(lambda value:json.dumps(broker.dispatch(value),separators=(',',':')).encode(),
        limit=2*1024*1024,name='broker',quiescence_error=RuntimeError('provider broker still in flight; not quiescent'))
