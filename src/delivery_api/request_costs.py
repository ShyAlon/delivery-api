"""Rolling, bounded, in-memory record of where request time went.

For the operator's Debug dialog and the everything ZIP (2026-10-26 latency
incident: 12-14s polls with 1.5s of DB time and no way to attribute the gap).
One server process: the numbers are this process's. Request types are route
templates - never raw task ids, so the view can be shared as-is."""
from __future__ import annotations

import math
import re
import threading
from collections import deque
from datetime import datetime, timezone

MAX_ENTRIES = 2000
FIELDS = ('duration_ms', 'db_ms', 'engine_ms', 'checkout_ms', 'auth_ms', 'held_ms', 'loop_blocked_ms', 'max_cycle_ms')

# Task ids are hex strings (12+ chars) or UUIDs; path segments that are pure numbers
# (generations, job numbers) are ids too. Everything else stays readable.
_ID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|[0-9a-f]{12,}|\d+', re.I)


def request_type(method, path):
    """The route template for grouping: method + path with every id segment replaced."""
    return f'{method} {_ID.sub("<id>", path)}'


_entries = deque(maxlen=MAX_ENTRIES)
_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def record(entry):
    with _lock:
        _entries.append(entry)


def record_request(method, path, status, duration_ms, stats):
    """One HTTP request with its measured decomposition. Called by the request-log middleware."""
    top_label, top_ms = stats.top_statement()
    from . import statement_trace
    statement_trace.fold_request(request_type(method, path), stats)
    record({'type': request_type(method, path), 'status': int(status), 'duration_ms': int(duration_ms),
            'db_ms': int(stats.db_seconds * 1000), 'db_queries': int(stats.queries),
            'engine_ms': int(stats.engine_seconds * 1000), 'engine_calls': int(stats.engine_calls),
            'checkout_ms': int(stats.checkout_seconds * 1000), 'auth_ms': int(stats.auth_seconds * 1000),
            'held_ms': int(stats.tx_held_seconds * 1000), 'loop_blocked_ms': 0, 'max_cycle_ms': 0,
            'top_statement': top_label, 'top_statement_ms': top_ms, 'at': _now()})


def record_stream(duration_ms, loop_blocked_ms, max_cycle_ms):
    """One closed progress stream: how long it lived and how long its synchronous DB reads
    blocked the event loop in total (the loop-block column exists for this row type)."""
    record({'type': 'GET /api/tasks/<id>/stream', 'status': 200, 'duration_ms': int(duration_ms),
            'db_ms': 0, 'db_queries': 0, 'engine_ms': 0, 'engine_calls': 0, 'checkout_ms': 0,
            'auth_ms': 0, 'held_ms': 0, 'loop_blocked_ms': int(loop_blocked_ms),
            'max_cycle_ms': int(max_cycle_ms), 'top_statement': None, 'top_statement_ms': 0, 'at': _now()})


def _percentile(values, p):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summary(slowest=10):
    """Per-type p50/p95 over the rolling window plus the slowest recent requests."""
    with _lock:
        entries = list(_entries)
    by_type = {}
    for entry in entries:
        by_type.setdefault(entry['type'], []).append(entry)
    types = []
    for name in sorted(by_type):
        rows = by_type[name]
        types.append({'type': name, 'count': len(rows),
                      'p50': {f: _percentile([r[f] for r in rows], 50) for f in FIELDS},
                      'p95': {f: _percentile([r[f] for r in rows], 95) for f in FIELDS}})
    slow = sorted(entries, key=lambda r: r['duration_ms'], reverse=True)[:slowest]
    from . import statement_trace
    return {'count': len(entries), 'since': entries[0]['at'] if entries else None,
            'types': types, 'slowest': slow, 'statement_trace': statement_trace.summary()}


def reset():
    """Tests only: drop the window."""
    from . import statement_trace
    statement_trace.reset()
    with _lock:
        _entries.clear()
