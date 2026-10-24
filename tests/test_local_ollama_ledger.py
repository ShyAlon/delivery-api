"""Local Ollama route through the real admitted wire against disposable Postgres.

(b) every local-route call reserves, dispatches and settles a $0 attempt row in the
shared ledger with its real usage; (a) a call whose (provider, model) has no reviewed
$0 row is refused by admission before any provider contact. Loopback-only fake Ollama
server; no network, no paid calls.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

pytest.importorskip('interview_engine', reason='interview-engine checkout required')
from test_postgres_store import store, DSN, A
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api import production_gateway as pg

pytestmark = pytest.mark.skipif(not DSN, reason='disposable Postgres required')

MODEL = 'qwen2.5-coder:14b'


def local_env(model, **over):
    row = {'provider': 'ollama', 'model': model, 'price_version': 'local-t1', 'source': 'test-local',
           'expense_input': 0, 'expense_output': 0, 'retail_input': 0, 'retail_output': 0,
           'retail_units_per_credit': 10**9}
    ceiling = {'model': model, 'context_tokens': 32768, 'maximum_output_tokens': 12288,
               'source': 'test-local', 'bound_version': 'local-t1', 'protective_run_ceiling': False}
    base = {pg.PRICES_ENV: json.dumps([row]), pg.CEILING_ENV: json.dumps(ceiling), 'DELIVERY_LOCAL_PREVIEW': '1'}
    base.update(over)
    return base


@pytest.fixture
def ollama_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['content-length']))
            requests.append((self.path, json.loads(body)))
            if self.path != '/v1/chat/completions':
                self.send_response(404); self.end_headers(); return
            payload = json.dumps({'id': 'fake', 'object': 'chat.completion', 'model': MODEL,
                                  'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
                                  'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}).encode()
            self.send_response(200)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, requests
    server.shutdown()
    server.server_close()


@pytest.fixture
def job(store):
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2) ON CONFLICT(owner_id) DO NOTHING', (A.owner_id, 'test-only'))
        c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES (%s,5000000000,1000000000,0) ON CONFLICT(owner_id) DO NOTHING', (A.owner_id,))
    with tenant_context(A):
        task = store.create_task('local-ollama-ledger-' + uuid.uuid4().hex)
        tid = task['task_id']
        store.set_engine_session(tid, 's-' + uuid.uuid4().hex)
        job_id = store.enqueue_worker(tid, 'phase:needs')
        store.claim_worker(job_id)
    yield store, tid, job_id
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM credit_charges WHERE owner_id=%s', (A.owner_id,))
        c.execute('DELETE FROM provider_attempts WHERE job_id=%s', (job_id,))
        c.execute('DELETE FROM admission_limits WHERE owner_id=%s', (A.owner_id,))
        c.execute('DELETE FROM credit_accounts WHERE owner_id=%s', (A.owner_id,))
    with tenant_context(A):
        store.finish_worker(job_id)
        if store.task(tid) is not None:
            store.delete_task(tid)


def _client(job, monkeypatch, ollama_server, row_model=MODEL, call_model=MODEL):
    store, _, job_id = job
    server, _ = ollama_server
    base = f'http://127.0.0.1:{server.server_port}/v1'
    monkeypatch.setenv('OLLAMA_BASE_URL', base)
    factory = pg.gateways_from_environment(store, local_env(row_model))
    gateway = factory(None, job_id)
    from delivery_api.provider_network import ollama_client_factory
    clients = ollama_client_factory(inner_sync=lambda: httpx.HTTPTransport(retries=0),
                                    inner_async=lambda: httpx.AsyncHTTPTransport(retries=0),
                                    owner_module='interview_engine')
    sync, _ = clients(stage='probing', provider='ollama', model=call_model, gateway=gateway)
    return factory, sync, base


def test_every_local_call_settles_a_zero_attempt_row_with_real_usage(job, monkeypatch, ollama_server):
    store, _, job_id = job
    _, requests = ollama_server
    factory, sync, base = _client(job, monkeypatch, ollama_server)
    try:
        with tenant_context(A):
            response = sync.post(base + '/chat/completions', json={'model': MODEL, 'max_tokens': 16,
                                                               'messages': [{'role': 'user', 'content': 'hi'}]})
            assert response.status_code == 200 and response.json()['choices'][0]['message']['content'] == 'ok'
    finally:
        sync.close(); factory.close()
    import psycopg, psycopg.rows
    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as c:
        rows = c.execute('SELECT state,expense_actual,retail_actual,usage FROM provider_attempts WHERE job_id=%s', (job_id,)).fetchall()
    assert len(requests) == 1 and requests[0][0] == '/v1/chat/completions'
    assert requests[0][1]['model'] == MODEL and requests[0][1]['max_tokens'] == 16
    assert len(rows) == 1, 'exactly one attempt row per call'
    row = rows[0]
    assert row['state'] == 'settled' and row['expense_actual'] == 0 and row['retail_actual'] == 0
    assert row['usage'] == {'input_tokens': 5, 'output_tokens': 2} or json.loads(row['usage']) == {'input_tokens': 5, 'output_tokens': 2}


def test_a_call_without_a_reviewed_row_is_refused_before_contact(job, monkeypatch, ollama_server):
    store, _, job_id = job
    _, requests = ollama_server
    # The reviewed $0 row names a DIFFERENT model: this call has no reviewed row.
    factory, sync, base = _client(job, monkeypatch, ollama_server, row_model='never-reviewed')
    try:
        with tenant_context(A):
            with pytest.raises(AdmissionDenied):
                sync.post(base + '/chat/completions', json={'model': MODEL, 'max_tokens': 16,
                                                        'messages': [{'role': 'user', 'content': 'hi'}]})
    finally:
        sync.close(); factory.close()
    import psycopg, psycopg.rows
    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as c:
        rows = c.execute('SELECT count(*) AS n FROM provider_attempts WHERE job_id=%s', (job_id,)).fetchone()
    assert requests == [], 'no provider contact when admission refuses'
    assert rows['n'] == 0
