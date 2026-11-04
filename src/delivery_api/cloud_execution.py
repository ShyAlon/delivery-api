"""Readiness of the same reviewed child runtime everywhere.

Readiness does not grant spending: durable owner admission, prepaid balance,
reviewed quotes and ceilings are enforced again at every dispatch.
"""
import os,sys

def problems(environ=None):
    from .cloud_runtime import check_gateways,failure
    out=[]
    try:check_gateways(os.environ if environ is None else environ)
    except Exception:out.append('reviewed child runtime is not ready')
    if failure():out.append('child runtime startup failed')
    return out

def admitted(environ=None):return not problems(environ)

def log_status(stream=None):
    reasons=problems()
    print('Execution: '+('READY (per-job owner and prepaid admission required)' if not reasons else 'UNAVAILABLE - '+'; '.join(reasons)),file=stream or sys.stderr)
