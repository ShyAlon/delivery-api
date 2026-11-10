"""What the database round trips of a request were FOR (2026-11-10).

One warm answer ran 323-374 statements and the debug evidence could only name the
slowest one. This keeps a bounded, rolling count per (request type, calling
purpose, statement shape), including the per-call overhead (identity, savepoint,
pool check, commit). Shapes come from statement_label (literals removed), request
types are route templates, purposes are function names: nothing here carries a
task id, a parameter or a secret, so the view can be shared as is.

Statements run inside a request are collected on its RequestStats and folded in
when the request ends; statements outside any request (background watchers and the
child supervisor) fold in at once under '(no request)'. Debug evidence only: it
changes no query."""
from __future__ import annotations

import sys
import threading

MAX_KEYS = 1500          # distinct (type, purpose, shape) keys kept; further ones are counted as dropped
MAX_PER_REQUEST = 2000   # entries one request may buffer before they fold in
NO_REQUEST = '(no request)'

# Modules whose frames are plumbing, and wrapper functions that say nothing about the purpose.
_PLUMBING = ('postgres_scope', 'db_pool', 'statement_trace', 'request_costs')
_GENERIC = {'_execute_native', 'call', 'guard', 'execute', 'transaction', '_begin', '__call__', '<lambda>',
            'note_statement', 'record', 'finish', 'finish_request', '_release', '_checkout', 'getconn'}
_ROOTS = ('delivery_api.', 'interview_engine.')

_lock = threading.Lock()
_agg = {}
_dropped = 0


def purpose(depth=3):
    """The nearest `depth` meaningful delivery/engine callers, innermost first."""
    frame = sys._getframe(2)
    found = []
    while frame is not None and len(found) < depth:
        module = frame.f_globals.get('__name__', '')
        name = frame.f_code.co_name
        if module.startswith(_ROOTS):
            short = module.rsplit('.', 1)[-1]
            if short not in _PLUMBING and name not in _GENERIC:
                found.append('%s:%s' % (short, name))
        frame = frame.f_back
    return '<'.join(found) or 'unattributed'


def record(kind, shape, seconds=0.0):
    """One round trip. kind: 'statement', 'pool_check' or 'commit'."""
    from . import postgres_scope
    entry = (purpose(), shape, int(seconds * 1000))
    stats = postgres_scope.request_stats.get()
    if stats is None:
        _fold(NO_REQUEST, [entry])
    elif len(stats.trace) < MAX_PER_REQUEST:
        stats.trace.append(entry)


def _fold(request_type, entries):
    global _dropped
    with _lock:
        for where, shape, ms in entries:
            key = (request_type, where, shape)
            row = _agg.get(key)
            if row is None:
                if len(_agg) >= MAX_KEYS:
                    _dropped += 1
                    continue
                row = _agg[key] = [0, 0]
            row[0] += 1
            row[1] += ms


LOG_MIN_ROUND_TRIPS = 40   # lighter requests are not worth a log line
LOG_ROWS = 30


def request_row_list(entries, limit=LOG_ROWS):
    """(count, ms, purpose, shape) per group, most frequent first."""
    grouped = {}
    for where, shape, ms in entries:
        row = grouped.setdefault((where, shape), [0, 0])
        row[0] += 1
        row[1] += ms
    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))[:limit]
    return [(n, ms, where, shape) for (where, shape), (n, ms) in ranked]


def request_rows(entries, limit=LOG_ROWS):
    """One request's round trips grouped by (purpose, shape), most frequent first, as 'count x ms purpose | shape' text."""
    grouped = {}
    for where, shape, ms in entries:
        row = grouped.setdefault((where, shape), [0, 0])
        row[0] += 1
        row[1] += ms
    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))[:limit]
    return ' ## '.join('%dx %dms %s | %s' % (n, ms, where, shape) for (where, shape), (n, ms) in ranked)


def fold_request(request_type, stats):
    entries = stats.trace
    if len(entries) >= LOG_MIN_ROUND_TRIPS:
        # The debug ZIP carries logs, not the admin request-costs view: a heavy request reports its own breakdown.
        from . import observability
        # One line per row: the log keeps 500 characters per field, and one joined 'rows' field lost everything after
        # the first five rows of the 845-round-trip answer (2026-11-10 debug ZIP).
        observability.event('db.statement_trace', 'Request round trips by purpose',
                            request_type=request_type, round_trips=len(entries), rows_logged=min(LOG_ROWS, len(request_row_list(entries))))
        for rank, (count, ms, where, shape) in enumerate(request_row_list(entries), 1):
            observability.event('db.statement_row', 'Round trips for one purpose',
                                request_type=request_type, rank=rank, count=count, ms=ms, purpose=where, shape=shape)
    _fold(request_type, entries)
    stats.trace = []


def summary(limit=60):
    """Per request type, the most frequent (purpose, shape) rows with count and summed ms."""
    with _lock:
        items = list(_agg.items())
        dropped = _dropped
    types = {}
    for (request_type, where, shape), (count, ms) in items:
        types.setdefault(request_type, []).append({'purpose': where, 'shape': shape, 'count': count, 'ms': ms})
    for rows in types.values():
        rows.sort(key=lambda r: (-r['count'], r['purpose'], r['shape']))
        del rows[limit:]
    return {'types': types, 'dropped': dropped, 'max_keys': MAX_KEYS,
            'note': 'count per request type since start; ms is summed over concurrent threads'}


def reset():
    """Tests only."""
    global _dropped
    with _lock:
        _agg.clear()
        _dropped = 0
