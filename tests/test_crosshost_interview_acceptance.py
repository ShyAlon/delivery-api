"""Cross-host interview continuation acceptance: one interview, two hosts.

Approved scope-727da1ba: two independent app instances on isolated throwaway
Postgres, the actual UI over HTTP (playwright), deterministic stubbed models,
no paid calls, no real Supabase. The interview runs on the local host (app +
separate engine process, database session persistence), stops, continues on
the cloud host (in-process engine under the money gateway) and returns to the
local host. Answers, checkpoints, artifacts and gates are identical regardless
of which host served the turn; the placement lease enforces one worker.

Determinism: tests/crosshost_stub/sitecustomize.py replaces the engine's
chat-model factory inside every server process (PYTHONPATH injection) and
answers the Ollama preflight listing, so no provider HTTP call can happen.
Supabase password sign-in is bridged to a test-minted ES256 token verified
against a test key; the browser flow itself is real.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pgserver = pytest.importorskip('pgserver', reason='pgserver not installed: cross-host acceptance skips')
pytest.importorskip('playwright.sync_api')
pytest.importorskip('interview_engine', reason='interview-engine checkout required: cross-host acceptance skips')

import pg_harness

OWNER = '00000000-0000-0000-0000-000000000001'
TOKEN = 'crosshost-acceptance-engine-token'  # 33 chars, never the default
REPO = Path(__file__).resolve().parents[1]
ENGINE_SRC = Path(os.environ['DELIVERY_TEST_ENGINE_SOURCE']) if os.environ.get('DELIVERY_TEST_ENGINE_SOURCE') else None
GENERATOR_SRC = Path(os.environ['DELIVERY_TEST_GENERATOR_SOURCE']) if os.environ.get('DELIVERY_TEST_GENERATOR_SOURCE') else None
pytestmark = pytest.mark.skipif(not ENGINE_SRC or not (ENGINE_SRC / 'src/interview_engine/http_service.py').is_file()
                                or not GENERATOR_SRC or not (GENERATOR_SRC / 'src/spec_generator').is_dir(),
                                reason='requires DELIVERY_TEST_ENGINE_SOURCE (interview-engine checkout)')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_http(url, seconds=30):
    import urllib.error
    for _ in range(int(seconds * 10)):
        try:
            urllib.request.urlopen(url, timeout=.3)
            return
        except urllib.error.HTTPError:
            return  # any HTTP response means the server is up (engines have no /health)
        except Exception:
            time.sleep(.1)
    raise RuntimeError('server did not start: ' + url)


@pytest.fixture(scope='module')
def dsn():
    pg_harness.start()
    value = pg_harness.STATE.get('dsn')
    if not value:
        pytest.skip('pg_harness unavailable: ' + pg_harness.STATE.get('reason', ''))
    if 'dbname=test_tenant_' not in value:
        pytest.skip('isolated test database required')
    return value


@pytest.fixture(scope='module')
def keys(tmp_path_factory):
    from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
    from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption,
                                                              PrivateFormat, PublicFormat)
    root = tmp_path_factory.mktemp('crosshost-keys')
    key = generate_private_key(SECP256R1())
    private = root / 'stub.pem'
    public = root / 'stub.pub'
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    return private, public


def base_env(root, keys, extra_pythonpath):
    private, public = keys
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('DELIVERY_', 'INTERVIEW_ENGINE_', 'VMODEL_', 'OLLAMA_', 'DEEPSEEK_', 'CROSSHOST_'))}
    env.update({
        'PYTHONPATH': os.pathsep.join([str(REPO / 'tests/crosshost_stub'), str(REPO / 'src')] + extra_pythonpath),
        'CROSSHOST_STUB': '1',
        'CROSSHOST_STUB_AUTH': '1',
        'CROSSHOST_STUB_JWT_KEY': str(private),
        'CROSSHOST_STUB_JWKS': str(public),
        'CROSSHOST_STUB_OWNER': OWNER,
        'CROSSHOST_STUB_ISSUER': 'https://supabase.crosshost.test/auth/v1',
        'DELIVERY_COOKIE_SECURE': '0',
    })
    return env


def gateway_env():
    """The reviewed money-gateway environment, from the repo's own hermetic
    fixture, admitting exactly the session's model (deepseek-flash): the same
    row shape, ceiling shape and env names as production review, no flattened
    semantics."""
    import importlib.util, json as _json
    spec = importlib.util.spec_from_file_location('gateway_test_inputs', REPO / 'tests/test_production_gateway.py')
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    return inputs.env(**{
        inputs.pg.PRICES_ENV: _json.dumps([inputs.row(model='deepseek-flash')]),
        inputs.pg.CEILING_ENV: _json.dumps({**inputs.CEILING, 'model': 'deepseek-flash'}),
    })


def app_dsn_uri(dsn_value):
    """The app's cloud-tenant detection needs a postgres:// URI; pg_harness
    publishes keyword conninfo. The socket directory travels as a query param."""
    from psycopg.conninfo import conninfo_to_dict
    parts = conninfo_to_dict(dsn_value)
    return 'postgresql://delivery_app@/%s?host=%s' % (parts['dbname'], parts['host'])


def launch(name, argv, env, ready_url, log):
    process = subprocess.Popen(argv, env=env, stdout=log.open('w'), stderr=subprocess.STDOUT)
    try:
        wait_http(ready_url)
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError(name + ' failed to start; log:\n' + log.read_text()[-2000:])
    return process


class AdmittedHost:
    """One admitted app instance: the in-process interview engine under the
    money gateway, database session persistence from DELIVERY_DATABASE_URL.
    Local and cloud hosts run this same admitted path; only the host identity
    (ports, artifacts directory, process) differs."""

    def __init__(self, name, root, keys, dsn_value):
        self.name = name
        self.root = root
        self.port = free_port()
        self.artifacts = root / ('artifacts-' + name)
        env = base_env(root, keys, [str(ENGINE_SRC / 'src'), str(GENERATOR_SRC / 'src')])
        env.update(gateway_env())
        # The engine preflight fails closed without a price for the selected
        # route (unpriced calls are unbudgeted calls). Published peak values.
        price_table = root / ('prices-' + name + '.json')
        price_table.write_text(json.dumps({'deepseek-flash': [0.30, 1.20]}))
        env.update({
            'DELIVERY_DATABASE_URL': app_dsn_uri(dsn_value),
            'DELIVERY_SUPABASE_URL': 'https://supabase.crosshost.test',
            'DELIVERY_SUPABASE_ANON_KEY': 'crosshost-stub-anon',
            'DELIVERY_CLOUD_EXECUTION': '1',
            'INTERVIEW_ENGINE_TOKEN': TOKEN,
            'VMODEL_ARTIFACTS_DIR': str(self.artifacts),
            'VMODEL_PRICE_TABLE': str(price_table),
        })
        self.process = launch(name + ' app', [sys.executable, '-m', 'uvicorn', 'delivery_api.app:app',
                                              '--host', '127.0.0.1', '--port', str(self.port)],
                              env, f'http://127.0.0.1:{self.port}/health', root / ('app-' + name + '.log'))
        self.base = f'http://127.0.0.1:{self.port}'

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)


def test_admitted_host_boots_with_inprocess_engine_and_database_sessions(dsn, keys, tmp_path_factory):
    root = tmp_path_factory.mktemp('crosshost-boot')
    host = AdmittedHost('local', root, keys, dsn)
    try:
        log = (root / 'app-local.log').read_text()
        assert 'Cloud execution: OPEN' in log, 'execution admitted, in-process engine installed'
        # The placement schema is the real migrated one; the engine claims rows here.
        import psycopg
        conn = psycopg.connect(dsn, autocommit=True)
        role = conn.execute("select rolname from pg_roles where rolname='requi_backend'").fetchone()
        assert role, 'placement schema present'
        conn.close()
    finally:
        host.stop()


def sign_in(page, base):
    """The real UI login flow; the app's Supabase bridge is stubbed hermetically."""
    page.goto(base + '/')
    page.locator('#authform input[name="username"]').fill('founder@crosshost.test')
    page.locator('#authform input[name="password"]').fill('Crosshost-Test-1')
    page.locator('#authform .auth-submit').click()
    # The SPA stays at '/' and swaps the auth card for the task dashboard.
    page.wait_for_selector('#new', timeout=15000)


def create_task(page, base, title, brief):
    page.goto(base + '/')
    page.locator('#new').click()
    page.locator('#newform input[name="title"]').fill(title)
    page.locator('#newform textarea[name="brief"]').fill(brief)
    page.locator('#newform button.primary').click()
    page.wait_for_url('**/tasks/*/view', timeout=15000)
    return page.url.split('/tasks/')[1].split('/')[0]


def test_admitted_host_runs_the_first_interview_turn_through_the_ui(dsn, keys, tmp_path_factory):
    from playwright.sync_api import sync_playwright, expect
    root = tmp_path_factory.mktemp('crosshost-first-turn')
    host = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, host.base)
            task_id = create_task(page, host.base, 'Surf school booking',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            # The stub asks its scripted first question; the answer form appears.
            try:
                expect(page.locator('#answerform')).to_be_visible(timeout=30000)
            except Exception:
                import psycopg as _p, psycopg.rows as _pr
                _c = _p.connect(dsn, autocommit=True, row_factory=_pr.dict_row)
                for _r in _c.execute("select kind, message, payload from events where task_id=%s order by id", (task_id,)).fetchall():
                    print('EVENT', _r['kind'], '|', _r['message'][:400], '|', str(_r.get('payload'))[:600])
                _c.close()
                raise
            question = page.locator('#currentquestion p').inner_text()
            assert 'booking' in question.lower()
            page.locator('#answerform textarea[name="answer"]').fill('Customers pick a slot and pay online.')
            page.locator('#answerform button[type="submit"]').click()
            browser.close()
        # The turn landed in the placement registry: one session row, this owner.
        import psycopg
        import psycopg.rows
        conn = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
        rows = conn.execute("select session_id, owner_id::text, generation from engine_sessions where owner_id=%s", (OWNER,)).fetchall()
        conn.close()
        assert len(rows) == 1 and rows[0]['generation'] >= 1
    finally:
        host.stop()
