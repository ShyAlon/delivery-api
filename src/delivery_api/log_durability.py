"""Bounded process-local evidence loss, no recursive logging or private paths."""
import sys
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_failures = {}


def degraded(source, error):
    with _lock:
        if source in _failures or len(_failures) >= 16:
            return
        _failures[source] = {'source': source, 'category': type(error).__name__,
                             'since': datetime.now(timezone.utc).isoformat()}
    try:
        sys.stderr.write('WARNING: diagnostic evidence degraded: %s (%s); operator Debug has status.\n' %
                         (source, type(error).__name__))
    except Exception:
        pass


def snapshot():
    with _lock:
        return {'degraded': bool(_failures), 'sources': [dict(row) for row in _failures.values()],
                'scope': 'this server process; a failure remains recorded until restart'}
