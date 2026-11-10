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
        # The authority check runs from many places; the 3 nearest frames stop inside it and hide who asked.
        if len(found) == depth and found[-1].endswith(':require_job') and depth < 6:
            depth += 2
    return '<'.join(found) or 'unattributed'


def record(kind, shape, seconds=0.0, syncs=1):
    """One trace entry. kind: 'statement', 'pool_check' or 'commit'. syncs is the network round trips it cost:
    1 normally, 0 for a statement that rode another statement's pipelined batch (BEGIN, identity, SAVEPOINT)."""
    from . import postgres_scope
    where = purpose()
    if where == 'unattributed':   # a worker thread has no delivery frame; its name says which pool it came from
        where = 'unattributed[thread=%s]' % threading.current_thread().name.split('_')[0]
    entry = (where, shape, int(seconds * 1000), syncs)
    stats = postgres_scope.request_stats.get()
    if stats is None:
        _fold(NO_REQUEST, [entry])
    elif len(stats.trace) < MAX_PER_REQUEST:
        stats.trace.append(entry)


def _fold(request_type, entries):
    global _dropped
    with _lock:
        for where, shape, ms, *_ in entries:
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
    for where, shape, ms, *_ in entries:
        row = grouped.setdefault((where, shape), [0, 0])
        row[0] += 1
        row[1] += ms
    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))[:limit]
    return [(n, ms, where, shape) for (where, shape), (n, ms) in ranked]


def request_rows(entries, limit=LOG_ROWS):
    """One request's round trips grouped by (purpose, shape), most frequent first, as 'count x ms purpose | shape' text."""
    grouped = {}
    for where, shape, ms, *_ in entries:
        row = grouped.setdefault((where, shape), [0, 0])
        row[0] += 1
        row[1] += ms
    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))[:limit]
    return ' ## '.join('%dx %dms %s | %s' % (n, ms, where, shape) for (where, shape), (n, ms) in ranked)


PURPOSE_ROWS = 80   # every purpose of a request, collapsed over statement shape

# First matching keyword in the purpose chain wins. Phases name where an answer spends its trips.
_PHASES = (('handler', ('answer_interview',)), ('authority', ('worker_capability', 'authority_snapshot')),
           ('watcher', ('stop_watch',)), ('engine_events', ('_record_engine_timing',)),
           ('admission', ('attempt_ledger', 'admit_provider', 'child_broker', 'child_provider_broker', 'prepaid', 'retail_ledger')),
           ('job', ('execute_job', 'claim_worker', 'child_interview', 'tenant_jobs')),
           ('progress', ('progress', 'ingest_interview')), ('model_pins', ('model_pins',)),
           ('pool', ('pg_pool',)))


def phase_of(purpose):
    for phase, words in _PHASES:
        if any(w in purpose for w in words):
            return phase
    return 'other'


def _syncs(entry):
    return entry[3] if len(entry) > 3 else 1


def purpose_rows(entries, limit=PURPOSE_ROWS):
    """Per purpose (shapes collapsed): entries, real network syncs, transactions (identity batches), ms. Most syncs first.
    Unlike request_row_list this is not cut to the 30 most frequent shapes, so the rows account for the request."""
    grouped = {}
    for entry in entries:
        where, shape, ms = entry[0], entry[1], entry[2]
        row = grouped.setdefault(where, [0, 0, 0, 0])
        row[0] += 1
        row[1] += _syncs(entry)
        row[2] += 1 if shape.startswith('SELECT set_config') else 0
        row[3] += ms
    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1][1], kv[0]))[:limit]
    return [(where, n, syncs, tx, ms) for where, (n, syncs, tx, ms) in ranked]


def phase_totals(entries):
    """{phase: [entries, syncs, transactions]} for one request."""
    totals = {}
    for entry in entries:
        row = totals.setdefault(phase_of(entry[0]), [0, 0, 0])
        row[0] += 1
        row[1] += _syncs(entry)
        row[2] += 1 if entry[1].startswith('SELECT set_config') else 0
    return totals


def fold_request(request_type, stats):
    entries = stats.trace
    if len(entries) >= LOG_MIN_ROUND_TRIPS:
        # The debug ZIP carries logs, not the admin request-costs view: a heavy request reports its own breakdown.
        from . import observability
        # One line per row: the log keeps 500 characters per field, and one joined 'rows' field lost everything after
        # the first five rows of the 845-round-trip answer (2026-11-10 debug ZIP).
        total_syncs = sum(_syncs(e) for e in entries)
        total_tx = sum(1 for e in entries if e[1].startswith('SELECT set_config'))
        observability.event('db.statement_trace', 'Request round trips by purpose',
                            request_type=request_type, round_trips=len(entries), rows_logged=min(LOG_ROWS, len(request_row_list(entries))),
                            network_syncs=total_syncs, transactions=total_tx)
        for rank, (count, ms, where, shape) in enumerate(request_row_list(entries), 1):
            observability.event('db.statement_row', 'Round trips for one purpose',
                                request_type=request_type, rank=rank, count=count, ms=ms, purpose=where, shape=shape)
        rows = purpose_rows(entries)
        for rank, (where, n, syncs, tx, ms) in enumerate(rows, 1):
            observability.event('db.purpose_row', 'Every round trip of one purpose, shapes collapsed',
                                request_type=request_type, rank=rank, entries=n, syncs=syncs, transactions=tx, ms=ms,
                                phase=phase_of(where), purpose=where)
        # The row sum is the request total: a ZIP reader can check nothing was cut.
        observability.event('db.answer_budget', 'Round trips of one request by phase',
                            request_type=request_type, entries=len(entries), network_syncs=total_syncs, transactions=total_tx,
                            purposes_logged=len(rows), purposes_total=len({e[0] for e in entries}),
                            **{('phase_%s' % p): '%d entries, %d syncs, %d tx' % tuple(v) for p, v in sorted(phase_totals(entries).items())})
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
