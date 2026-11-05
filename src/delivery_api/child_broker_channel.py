"""Private parent provider channel, opt-in constructor seam only."""
import json
from contextlib import contextmanager
from .child_channel import json_channel
from .child_provider_broker import ProviderRefused


@contextmanager
def provider_broker_channel(broker):
    # Keep the existing wire protocol and permanently-poisoned child client.
    # The parent already owns the safe refusal, so no raw error frame or child
    # exception text needs to be trusted or sent back over the private socket.
    refusal=[]
    def dispatch(value):
        try:return json.dumps(broker.dispatch(value),separators=(',',':')).encode()
        except BaseException as error:
            # A child sees only channel EOF. Preserve safe parent-side cause
            # classes/frames in the container log, never values or SDK payloads.
            import sys
            from .child_failure import project
            print('parent broker failure '+json.dumps(project(error),separators=(',',':')),file=sys.stderr,flush=True)
            if isinstance(error,ProviderRefused):refusal.append(error)
            raise
    try:
        with json_channel(dispatch,limit=2*1024*1024,name='broker',
                quiescence_error=RuntimeError('provider broker still in flight; not quiescent')) as channel:
            yield channel
    except BaseException as error:
        # json_channel exits first. Never hide failure to prove quiescence.
        if refusal and str(error)!='provider broker still in flight; not quiescent':
            raise refusal[0] from error
        raise
    if refusal:raise refusal[0]
