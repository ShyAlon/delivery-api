"""The operator's request-cost view (2026-10-26 latency incident).

The decomposed timings (duration, db, engine, checkout, auth, held, loop block)
are only useful where the operator looks: a rolling, bounded, in-memory window
behind the Debug dialog, per request type (route templates, never raw task ids),
with p50/p95 per metric and the slowest recent requests, and a text rendering
that lands in the everything ZIP."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from delivery_api import request_costs

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src' / 'delivery_api' / 'web'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='needs node')


def _node(source):
    result = subprocess.run(['node', '--input-type=module', '-e', source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(autouse=True)
def _clean():
    request_costs.reset()
    yield
    request_costs.reset()


def _entry(type_, duration, **kw):
    return {'type': type_, 'status': kw.get('status', 200), 'duration_ms': duration,
            'db_ms': kw.get('db', 0), 'db_queries': kw.get('q', 0),
            'engine_ms': kw.get('engine', 0), 'engine_calls': kw.get('ecalls', 0),
            'checkout_ms': kw.get('checkout', 0), 'auth_ms': kw.get('auth', 0),
            'held_ms': kw.get('held', 0), 'loop_blocked_ms': kw.get('loop', 0),
            'max_cycle_ms': kw.get('max_cycle', 0), 'top_statement': None, 'top_statement_ms': 0,
            'at': kw.get('at', '2026-10-26T20:00:00Z')}


def test_request_type_strips_raw_ids():
    assert request_costs.request_type('GET', '/api/tasks/170d45b6bdd7e28f/phase-output') == 'GET /api/tasks/<id>/phase-output'
    assert request_costs.request_type('GET', '/api/tasks/00000000-0000-0000-0000-0000000000aa') == 'GET /api/tasks/<id>'
    assert request_costs.request_type('POST', '/api/tasks/01dc1672e28f/answer') == 'POST /api/tasks/<id>/answer'


def test_summary_groups_per_type_with_percentiles():
    for d in (100, 200, 300, 400, 5000):
        request_costs.record(_entry('GET /api/tasks/<id>', d, db=50, engine=d - 60))
    request_costs.record(_entry('GET /api/account/ledger', 900, db=900))
    s = request_costs.summary()
    row = next(r for r in s['types'] if r['type'] == 'GET /api/tasks/<id>')
    assert row['count'] == 5 and row['p50']['duration_ms'] == 300 and row['p95']['duration_ms'] == 5000
    assert row['p95']['engine_ms'] == 4940
    ledger = next(r for r in s['types'] if r['type'] == 'GET /api/account/ledger')
    assert ledger['count'] == 1


def test_slowest_recent_requests_carry_the_decomposition_without_raw_ids():
    request_costs.record(_entry('GET /api/tasks/<id>', 12542, db=1468, engine=10800, checkout=3, auth=120, held=12400))
    request_costs.record(_entry('GET /api/account/ledger', 2900, db=930))
    s = request_costs.summary()
    assert s['slowest'][0]['duration_ms'] == 12542 and s['slowest'][0]['engine_ms'] == 10800
    assert '170d45' not in json.dumps(s) and '01dc1672' not in json.dumps(s)


def test_window_is_bounded():
    for i in range(request_costs.MAX_ENTRIES + 50):
        request_costs.record(_entry('GET /api/tasks/<id>', i))
    s = request_costs.summary()
    assert s['count'] == request_costs.MAX_ENTRIES
    assert s['since'] is not None


def test_stream_loop_block_is_a_visible_type():
    request_costs.record_stream(duration_ms=60000, loop_blocked_ms=4100, max_cycle_ms=700)
    s = request_costs.summary()
    row = next(r for r in s['types'] if r['type'] == 'GET /api/tasks/<id>/stream')
    assert row['count'] == 1 and row['p95']['loop_blocked_ms'] == 4100


def test_endpoint_returns_the_summary():
    import delivery_api.app as module
    request_costs.record(_entry('GET /api/tasks/<id>', 100, db=10))
    out = module.admin_operations_request_costs(a=type('A', (), {'label': 'op'})())
    assert out['count'] == 1 and out['types'][0]['type'] == 'GET /api/tasks/<id>'
    assert set(out['types'][0]['p50']) >= {'duration_ms', 'db_ms', 'engine_ms', 'checkout_ms', 'auth_ms', 'held_ms', 'loop_blocked_ms'}


EXPECTED_COLUMNS = ['Request', 'Requests', 'P50 Duration Ms', 'P95 Duration Ms', 'P50 DB Ms', 'P95 DB Ms',
    'P50 Engine Ms', 'P95 Engine Ms', 'P50 Checkout Ms', 'P95 Checkout Ms', 'P50 Auth Ms', 'P95 Auth Ms',
    'P50 Held Ms', 'P95 Held Ms', 'P50 Loop Block Ms', 'P95 Loop Block Ms']


@needs_node
def test_request_cost_table_helpers_title_case_sortable_and_exportable():
    digest = (WEB / 'debug-digest.js').as_uri()
    src = """
const m=await import('%s');
const data={count:2,since:'2026-10-26T20:00:00Z',types:[
 {type:'GET /api/account/ledger',count:1,p50:{duration_ms:900,db_ms:900,engine_ms:0,checkout_ms:0,auth_ms:5,held_ms:880,loop_blocked_ms:0},p95:{duration_ms:900,db_ms:900,engine_ms:0,checkout_ms:0,auth_ms:5,held_ms:880,loop_blocked_ms:0}},
 {type:'GET /api/tasks/<id>',count:5,p50:{duration_ms:300,db_ms:50,engine_ms:200,checkout_ms:1,auth_ms:5,held_ms:280,loop_blocked_ms:0},p95:{duration_ms:5000,db_ms:60,engine_ms:4940,checkout_ms:2,auth_ms:9,held_ms:4900,loop_blocked_ms:0}}],
 slowest:[{type:'GET /api/tasks/<id>',at:'2026-10-26T20:32:38Z',status:200,duration_ms:12542,db_ms:1468,engine_ms:10800,checkout_ms:3,auth_ms:120,held_ms:12400,loop_blocked_ms:0}]};
const labels=m.requestCostColumns().map(c=>c.label);
const expected=%s;
if(JSON.stringify(labels)!==JSON.stringify(expected))throw new Error('columns: '+JSON.stringify(labels));
const rows=m.requestCostRows(data);
if(rows.length!==2||rows[0].p95_engine_ms!==0)throw new Error('row flattening failed: '+JSON.stringify(rows[0]));
const sorted=m.sortRequestCostRows(rows,'p95_duration_ms',false);
if(sorted[0].type!=='GET /api/tasks/<id>')throw new Error('sort by p95 duration desc failed');
const asc=m.sortRequestCostRows(rows,'p95_duration_ms',true);
if(asc[0].type!=='GET /api/account/ledger')throw new Error('sort ascending failed');
if(rows[0].type!=='GET /api/account/ledger')throw new Error('sort must not mutate the input');
const text=m.formatRequestCostsText(data);
if(!text.includes('GET /api/tasks/<id>'))throw new Error('text export lost the type');
if(/170d45|01dc1672/.test(text))throw new Error('raw id leaked into the text export');
if(!text.includes('Slowest Recent Requests'))throw new Error('text export lost the slowest section');
console.log('ok');
""" % (digest, json.dumps(EXPECTED_COLUMNS))
    assert _node(src).strip() == 'ok'


def test_stream_max_cycle_is_stored_in_the_window():
    request_costs.record_stream(duration_ms=60000, loop_blocked_ms=4100, max_cycle_ms=700)
    s = request_costs.summary()
    row = next(r for r in s['types'] if r['type'] == 'GET /api/tasks/<id>/stream')
    assert row['p95']['max_cycle_ms'] == 700
    slow = next(r for r in s['slowest'] if r['type'] == 'GET /api/tasks/<id>/stream')
    assert slow['max_cycle_ms'] == 700
