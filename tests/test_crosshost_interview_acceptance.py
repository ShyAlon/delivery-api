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
    grant_founder_credits(value)
    return value


def grant_founder_credits(dsn_value):
    """The admission gate needs an admission row and a prepaid balance per
    owner; the harness seeds owners with neither. Grant through the migration's
    own operator machinery (20261020150000): a principal row for the founder,
    then the SECURITY DEFINER grant function under the founder's claims -
    the same path the operator page drives, never a direct balance write."""
    import psycopg
    conn = psycopg.connect(dsn_value, autocommit=True)
    conn.execute(
        "INSERT INTO operator_principals(subject, registry_version, capabilities, enabled)"
        " VALUES (%s, 'ops-v1', ARRAY['credits.allocate'], true)"
        " ON CONFLICT (subject) DO NOTHING", (OWNER,))
    conn.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (OWNER,))
    conn.execute("SELECT set_config('request.jwt.claim.session_id', %s, false)", ('00000000-0000-0000-0000-0000000000a1',))
    result = conn.execute(
        "SELECT public.requi_operator_grant_credits(%s, %s, %s)",
        ('owner-a@example.test', 500, 'crosshost acceptance: founder prepaid balance')).fetchone()
    conn.close()
    assert result and result[0].get('granted'), 'operator grant through the migration function: %s' % (result,)


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
    if os.environ.get('DELIVERY_TEST_RUN_ISOLATION'):
        env['DELIVERY_RUN_ISOLATION'] = os.environ['DELIVERY_TEST_RUN_ISOLATION']
    env.update({
        'PYTHONPATH': os.pathsep.join([str(REPO / 'tests/crosshost_stub'), str(REPO / 'src')] + extra_pythonpath),
        'CROSSHOST_STUB': '1',
        'CROSSHOST_STUB_AUTH': '1',
        'CROSSHOST_STUB_JWT_KEY': str(private),
        'CROSSHOST_STUB_JWKS': str(public),
        'CROSSHOST_STUB_OWNER': OWNER,
        # The stub JWT carries the real seeded auth session for OWNER (pg_harness
        # seeds sessions distinct from user ids): session authority is never the
        # owner literal.
        'CROSSHOST_STUB_SESSION': '00000000-0000-0000-0000-0000000000a1',
        'CROSSHOST_STUB_ISSUER': 'https://supabase.crosshost.test/auth/v1',
        'DELIVERY_COOKIE_SECURE': '0',
        # Temporary hang diagnosis passthrough (remove before final).
    })
    return env


def gateway_env():
    """The reviewed money-gateway environment for the approved local route:
    the repo's own local-Ollama fixture row (one reviewed $0 local model, no
    provider key), so generator phases can run in a child process in the
    deployed-default process isolation. Test environment only."""
    import importlib.util, json as _json
    spec = importlib.util.spec_from_file_location('local_route_test_inputs', REPO / 'tests/test_local_ollama_route.py')
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    return inputs.local_env()


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

    def __init__(self, name, root, keys, dsn_value, artifacts=None, extra_env=None):
        self.name = name
        self.root = root
        self.port = free_port()
        self.artifacts = artifacts or (root / ('artifacts-' + name))
        env = base_env(root, keys, [str(ENGINE_SRC / 'src'), str(GENERATOR_SRC / 'src')])
        env.update(gateway_env())
        # The engine preflight fails closed without a price for the selected
        # route (unpriced calls are unbudgeted calls). Published peak values.
        price_table = root / ('prices-' + name + '.json')
        price_table.write_text(json.dumps({'deepseek-flash': [0.30, 1.20], 'qwen2.5-coder:14b': [0.0, 0.0]}))
        env.update({
            'DELIVERY_DATABASE_URL': app_dsn_uri(dsn_value),
            'DELIVERY_SUPABASE_URL': 'https://supabase.crosshost.test',
            'DELIVERY_SUPABASE_ANON_KEY': 'crosshost-stub-anon',
            'DELIVERY_CLOUD_EXECUTION': '1',
            'INTERVIEW_ENGINE_TOKEN': TOKEN,
            'VMODEL_ARTIFACTS_DIR': str(self.artifacts),
            'VMODEL_PRICE_TABLE': str(price_table),
            # Where the orchestrator finds the generator checkout (its
            # .venv/bin/python must exist even for in-process phases).
            'SPEC_GENERATOR_DIR': str(GENERATOR_SRC),
            # The reviewed local route's model, aligned across the engine
            # session catalog, generator phase defaults and the gateway row.
            'DELIVERY_OLLAMA_MODEL': 'qwen2.5-coder:14b',
            # Far above every drive timeout: no handoff in this test can be
            # explained by lease expiry - only by the graceful release.
            'INTERVIEW_ENGINE_LEASE_TTL': '300',
        })
        env.update(extra_env or {})
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





def answer_question(page, text):
    """Answer the currently rendered interview question and wait for the next
    state: either the next question form or the interview-complete view."""
    from playwright.sync_api import expect
    expect(page.locator('#answerform')).to_be_visible(timeout=60000)
    page.locator('#answerform textarea[name="answer"]').fill(text)
    page.locator('#answerform button[type="submit"]').click()


def placement(dsn_value, session_id):
    """The live placement row: which host instance serves this session now."""
    import psycopg, psycopg.rows
    conn = psycopg.connect(dsn_value, autocommit=True, row_factory=psycopg.rows.dict_row)
    row = conn.execute(
        "select placement_instance, lease_epoch, generation from engine_sessions where session_id=%s",
        (session_id,)).fetchone()
    conn.close()
    return row


def wait_gate_review(page, base, task_id, gate_key, timeout=120):
    """Poll the gate review API until the gate is actionable; return the JSON."""
    import time as _time, json as _json
    deadline = _time.time() + timeout
    last = None
    while _time.time() < deadline:
        response = page.request.get('%s/api/tasks/%s/gates/%s/review' % (base, task_id, gate_key))
        if response.ok:
            data = response.json()
            last = data
            if data.get('actionable'):
                return data
        _time.sleep(2)
    raise AssertionError('gate %s never became actionable: %s' % (gate_key, str(last)[:600]))


def gate_documents(review):
    """The evidence content of a gate review, compared across hosts. The review
    endpoint nests the evidence under its 'review' key."""
    inner = review.get('review') or {}
    return {doc['name']: doc.get('view', {}).get('content') for doc in inner.get('documents', [])}


def approve_gate(page, base, task_id, gate_key):
    from playwright.sync_api import expect
    page.goto('%s/tasks/%s/gates/%s' % (base, task_id, gate_key))
    button = page.locator('button[data-action="approve"]')
    expect(button).to_be_enabled(timeout=60000)
    button.click()
    # A recorded decision navigates back to the task view.
    page.wait_for_url('**/tasks/*/view**', timeout=60000)


class TurnTracker:
    """Answer each engine input exactly once, keyed by its durable input_id.
    Question text is not a turn signal: the coverage bank can legitimately
    re-ask the same text under a new input_id (and a restarted stub once did),
    and each such input must be answered on its own."""
    def __init__(self):
        self._seen = set()

    def should_answer(self, input_id):
        if not input_id or input_id in self._seen:
            return False
        self._seen.add(input_id)
        return True


def pump_interview(page, base, task_id, answers, *, max_turns=None, finish=True, timeout=240, shots=None):
    """Drive the interview through the real UI, keyed on the task's live
    input_state (not the DOM question text): plain questions get scripted text
    answers through #answerform, structured founder decisions (the conops
    concept CHOICE and the intent-model APPROVAL) are decided through the real
    decision pages. With finish=True, runs until the app reconciles completion
    (intent+conops approved; phase gates stay idle because the founder
    approved inside the interview). With max_turns=N, returns after N text
    answers once the engine waits for the next input - a stable checkpoint
    boundary the caller can safely stop the host at.
    Deterministic: the stub model's script saturates coverage and closes the
    interview; the coverage bank owns question text when a candidate fails
    verification."""
    import time as _time
    deadline = _time.time() + timeout
    tracker = TurnTracker()
    turns = 0
    def _errors():
        ev = page.request.get(base + '/api/tasks/' + task_id + '/events').json()
        ev = ev if isinstance(ev, list) else ev.get('events', [])
        return [e for e in ev if e.get('kind') == 'error']

    def fail_fast_on_rejection(before, waited_state):
        # A deterministic 422/protocol rejection never succeeds on retry; the
        # driver must surface the first error with evidence instead of
        # storming the same answer against the same wait until the timeout.
        deadline_ff = _time.time() + 8
        while _time.time() < deadline_ff:
            page.wait_for_timeout(400)
            errors = _errors()
            if len(errors) > before:
                raise AssertionError('engine rejected the answer deterministically: %r state=%r' % (
                    [str(e.get('message'))[:200] for e in errors[before:]], waited_state))
            current = page.request.get(base + '/api/tasks/' + task_id).json().get('input_state') or {}
            if current.get('input_id') != waited_state.get('input_id'):
                return  # accepted: the wait advanced

    decision_facts = {}
    page._drive_facts = decision_facts  # callers print this on any drive failure
    try:page.on('pageerror', lambda exc: decision_facts.setdefault('page_errors', []).append(str(exc)[:300]))
    except Exception:pass
    try:page.on('console', lambda msg: decision_facts.setdefault('console_warns', []).append(msg.text[:200])
                 if msg.type in ('warning', 'error') else None)
    except Exception:pass
    try:page.on('framenavigated', lambda frame: decision_facts.setdefault('navigations', []).append(frame.url[:200])
                 if frame == page.main_frame else None)
    except Exception:pass
    def capture_decision_facts(tag):
        # Why is nothing clickable on this decision page? Read-only rendering
        # (actionable=false), a render failure, or a driver predicate miss.
        facts = {'tag': tag, 'url': page.url}
        try:
            facts['reviewform_count'] = page.locator('#reviewform').count()
            # The post-commit strand signature: 'Decision was accepted, but the
            # next view could not be loaded' leaves the old form disabled with
            # no recovery while the live decision has already advanced.
            facts['review_status'] = page.locator('#review-status').inner_text()[:200] if page.locator('#review-status').count() else None
            facts['applied_class'] = 'applied' in (page.locator('#reviewform').get_attribute('class') or '') if facts['reviewform_count'] else None
            facts['readonly_notice'] = page.locator('.notice').first.inner_text()[:120] if page.locator('.notice').count() else None
            facts['buttons'] = [(b.inner_text()[:30], b.is_enabled()) for b in page.locator('#reviewform button').all()[:8]] if facts['reviewform_count'] else None
            facts['radios'] = page.locator('input[type="radio"]').count()
        except Exception as exc:
            facts['dom_error'] = str(exc)[:120]
        try:
            from urllib.parse import urlparse, parse_qs
            parts = urlparse(page.url)
            wait_id = parts.path.rstrip('/').split('/')[-1]
            revision = (parse_qs(parts.query).get('revision') or [None])[0]
            api = page.request.get(base + '/api/tasks/' + task_id + '/decisions/' + wait_id,
                                   params={'revision': revision} if revision else {})
            if api.ok:
                data = api.json()
                facts['actionable'] = data.get('actionable')
                facts['review_revision'] = (data.get('review') or {}).get('revision')
                facts['current_revision'] = (data.get('current') or {}).get('revision')
                facts['current_wait_id'] = (data.get('current') or {}).get('wait_id')
            else:
                facts['api_status'] = api.status
        except Exception as exc:
            facts['api_error'] = str(exc)[:120]
        decision_facts[tag] = facts
        return facts

    while _time.time() < deadline:
        task = page.request.get(base + '/api/tasks/' + task_id).json()
        phases = {x['phase_key']: x['state'] for x in task.get('phases', [])}
        # Terminal boundary: the interview's own founder approvals reconcile
        # intent+conops to approved; the phase gates stay idle because the
        # approval happened inside the interview. The next phase must NOT
        # start on its own (it spends credits).
        if phases.get('intent') in ('approved', 'done') and phases.get('conops') in ('approved', 'done'):
            return turns
        state = task.get('input_state') or {}
        if max_turns is not None and turns >= max_turns and state.get('waiting'):
            return turns
        if not finish and state.get('waiting') and state.get('decision'):
            return turns  # a decision is open; the caller asked for text turns only
        if state.get('waiting') and state.get('decision'):
            if '/decisions/' not in page.url:
                # The live view renders a sticky launcher that navigates to
                # the full decision page.
                launcher = page.locator('#opendecision')
                if launcher.count() and launcher.is_visible():
                    launcher.click()
                    page.wait_for_url('**/decisions/**', timeout=30000)
                page.wait_for_timeout(500)
                continue
            # Decision page: concept CHOICE (radio cards + rationale) or the
            # intent-model APPROVAL review. Pick the first option, give a
            # rationale where the form asks for one, continue.
            radios = page.locator('input[type="radio"]')
            if radios.count():
                radios.first.check()
                rationale = page.locator('textarea[name="rationale"]')
                if rationale.count() and rationale.is_visible():
                    rationale.fill('Deterministic acceptance: first option fits.')
            errors_before = len(_errors())
            clicked = False
            for label in ('Choose and continue', 'Proceed', 'Approve', 'Continue'):
                button = page.get_by_role('button', name=label, exact=True)
                if button.count() and button.first.is_enabled():
                    button.first.click()
                    clicked = True
                    break
            if not clicked and 'first_unclickable' not in decision_facts:
                capture_decision_facts('first_unclickable')
            page.wait_for_timeout(1000)
            fail_fast_on_rejection(errors_before, state)
            continue
        elif state.get('waiting') and state.get('question'):
            form = page.locator('#answerform')
            if form.count() and form.is_visible():
                area = page.locator('#answerform textarea[name="answer"]')
                if not area.is_disabled() and tracker.should_answer(state.get('input_id')):
                    try:
                        area.fill(answers[turns % len(answers)], timeout=15000)
                    except Exception:
                        raise
                    errors_before = len(_errors())
                    page.locator('#answerform button[type="submit"]').click()
                    fail_fast_on_rejection(errors_before, state)
                    turns += 1
                    continue
        page.wait_for_timeout(500)
    if shots:
        try:page.screenshot(path=str(shots / ('stall-%s.png' % (base.split(':')[-1],))), full_page=True)
        except Exception:pass
    if '/decisions/' in page.url:
        try:capture_decision_facts('stall')
        except Exception:pass
    task = page.request.get(base + '/api/tasks/' + task_id).json()
    events = page.request.get(base + '/api/tasks/' + task_id + '/events').json()
    tail = (events if isinstance(events, list) else events.get('events', []))[-12:]
    raise AssertionError('interview did not complete within %ss after %d turns\n'
        'state=%r active=%r phases=%r gates=%r input_state=%r\ndecision_facts=%r\nevents=%s' % (
        timeout, turns, task.get('state'), task.get('active_phase'),
        [(x['phase_key'], x['state']) for x in task.get('phases', [])],
        [(g['gate_key'], g['state']) for g in task.get('gates', [])],
        task.get('input_state'), decision_facts,
        [(e.get('kind'), str(e.get('message'))[:100]) for e in tail]))


def drive_interview(page, base, task_id, answers, timeout=240, shots=None):
    """Full completion drive: every text turn and both founder decisions."""
    return pump_interview(page, base, task_id, answers, finish=True, timeout=timeout, shots=shots)


def test_interview_completes_single_host_reference(dsn, keys, tmp_path_factory):
    """Reference drive on one host: the scripted interview runs to completion,
    the terminal handoff is recorded, and the intent gate becomes reviewable."""
    from playwright.sync_api import sync_playwright, expect
    root = tmp_path_factory.mktemp('crosshost-complete')
    host = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, host.base)
            task_id = create_task(page, host.base, 'Surf school booking',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
                       'Rainy days are automatically refunded.', 'Owners see a daily roster.',
                       'Instructors confirm from their phone.', 'Weekly payouts are automatic.']
            shots = root / 'shots'; shots.mkdir(exist_ok=True)
            try:
                turns = drive_interview(page, host.base, task_id, answers, shots=shots)
            except Exception:
                print('DRIVE_FACTS', getattr(page, '_drive_facts', {}))
                raise
            print('TURNS', turns)
            task = page.request.get(host.base + '/api/tasks/' + task_id).json()
            print('FINAL', task.get('state'), task.get('active_phase'),
                  [(p['phase_key'], p['state']) for p in task.get('phases', [])],
                  [(g['gate_key'], g['state']) for g in task.get('gates', [])])
            events = page.request.get(host.base + '/api/tasks/' + task_id + '/events').json()
            for event in (events if isinstance(events, list) else events.get('events', []))[-10:]:
                print('EV', event.get('kind'), str(event.get('message'))[:120])
            assert turns >= 2, 'the scripted interview took real turns'
            task = page.request.get(host.base + '/api/tasks/' + task_id).json()
            phases = {x['phase_key']: x['state'] for x in task['phases']}
            assert phases['intent'] == 'approved' and phases['conops'] == 'approved', phases
            assert phases['needs'] == 'pending', 'the spend-gated next phase must not auto-start'
            assert (task.get('input_state') or {}).get('engine_process_status') == 40, \
                'engine reports the session COMPLETED'
            # Persisted, validated artifacts under the configured root, bound
            # to this session: the same files the terminal reconcile verified.
            session_id = None
            import psycopg as _pg
            conn = _pg.connect(dsn, autocommit=True)
            row = conn.execute("select session_id from engine_sessions where task_id=%s", (task_id,)).fetchone()
            conn.close()
            session_id = row[0]
            base_dir = host.artifacts / 'tenants' / OWNER / 'sessions' / session_id / 'interview'
            intent_doc = (base_dir / 'intent_model.json').read_text()
            conops_doc = (base_dir / 'conops.json').read_text()
            assert session_id in intent_doc and session_id in conops_doc, \
                'artifacts are bound to the completing session'
            browser.close()
    finally:
        host.stop()


def test_interview_survives_local_cloud_local_with_one_lease_holder(dsn, keys, tmp_path_factory):
    """The approved cross-host topology: two independent admitted app instances
    (same in-process engine path, same throwaway PG), the actual UI over HTTP.

    The interview starts on the local host (turn 1), the cloud host resumes it
    mid-interview (turn 2) only after the local lease lapses, and the local
    host takes it back mid-interview again and drives it to completion (turn 3
    plus both founder decisions) - bidirectional continuation from committed
    boundaries, not post-completion inspection. One lease holder at every
    instant, enforced by the lease row, never by convention; placement rows
    and engine content agree at every hop."""
    from playwright.sync_api import sync_playwright
    import psycopg, psycopg.rows
    root = tmp_path_factory.mktemp('crosshost-drive')
    answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
               'Rainy days are automatically refunded.', 'Owners see a daily roster.']
    answered = []

    def session_id():
        conn = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
        row = conn.execute("select engine_session_id from tasks where task_id=%s", (task_id,)).fetchone()
        conn.close()
        return row['engine_session_id']

    def answer_events():
        conn = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
        rows = conn.execute(
            "select payload from events where task_id=%s and kind='answer' order by id", (task_id,)).fetchall()
        conn.close()
        import json as _json
        return [_json.loads(r['payload'])['answer'] if isinstance(r['payload'], str)
                else r['payload']['answer'] for r in rows]

    local = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local.base)
            task_id = create_task(page, local.base, 'Surf school booking (two-host)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            pump_interview(page, local.base, task_id, answers, max_turns=1, finish=False)
            browser.close()
        sid = session_id()
        first = placement(dsn, sid)
        assert first and first['placement_instance'], 'the local host holds the lease'
    finally:
        local.stop()
    answered.append(answers[0])

    import time as _time
    # While the local lease is still live, a cloud instance must not serve the
    # session: the lease row, not politeness, is the single-writer boundary.
    cloud = AdmittedHost('cloud', root, keys, dsn)
    try:
        held = placement(dsn, sid)
        assert held['placement_instance'] == first['placement_instance']
        # No expiry wait: the outgoing host's graceful shutdown released the
        # lease at a committed boundary (TTL 300s rules expiry out).
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud.base)
            page.goto(cloud.base + '/tasks/%s/view' % task_id)
            pump_interview(page, cloud.base, task_id, answers[1:], max_turns=1, finish=False)
            browser.close()
        second = placement(dsn, sid)
        assert second['placement_instance'] != first['placement_instance'], \
            'cloud claimed the session after the local lease lapsed'
        assert second['lease_epoch'] > first['lease_epoch'], 'takeover bumped the lease epoch'
    finally:
        cloud.stop()
    answered.append(answers[1])

    # Back to the local host mid-interview: it must continue the same session
    # (turn 3 and both decisions), not inspect a finished one. Again no expiry
    # wait - the cloud host's shutdown released the lease.
    local2 = AdmittedHost('local2', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local2.base)
            page.goto(local2.base + '/tasks/%s/view' % task_id)
            page.wait_for_timeout(3000)
            shots = root / 'shots'
            shots.mkdir(exist_ok=True)
            drive_interview(page, local2.base, task_id, answers[2:], shots=shots)
            page.goto(local2.base + '/tasks/%s/view' % task_id)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(shots / 'complete.png'), full_page=True)
            task = page.request.get(local2.base + '/api/tasks/' + task_id).json()
            browser.close()
        third = placement(dsn, sid)
        assert third['placement_instance'] != second['placement_instance'], \
            'local reclaimed the session for the remaining turns'
        assert third['lease_epoch'] > second['lease_epoch']
        phases = {x['phase_key']: x['state'] for x in task['phases']}
        assert phases['intent'] == 'approved' and phases['conops'] == 'approved', phases
        assert phases['needs'] == 'pending', 'the spend-gated next phase must not auto-start'
        # Every text answer given on the earlier hosts is in the shared
        # transcript, in order (later events are the local2 turns and the two
        # decision submissions, which also post as answer events).
        assert answer_events()[:2] == answered, 'answers from the earlier hosts persisted in order'
        assert answers[2] in answer_events(), 'the reclaiming host added its own turn'
        # The completing host's artifacts are bound to the one session that
        # travelled across all three placements.
        base_dir = local2.artifacts / 'tenants' / OWNER / 'sessions' / sid / 'interview'
        assert sid in (base_dir / 'intent_model.json').read_text()
        assert sid in (base_dir / 'conops.json').read_text()
    finally:
        local2.stop()


def test_answer_composer_shadow_tracks_input_id_not_question_text(dsn, keys, tmp_path_factory):
    """Regression for the confirmed cross-host UI defect: after an answer is
    sent, the UI shows a 'Processing your answer...' shadow that hides the
    composer until the next input is served. The shadow used to key on the
    question TEXT, so a coverage-bank re-ask (same text, new input_id - a
    legitimate, observed engine behavior) left the composer hidden forever
    and the interview unanswerable through the real UI. The shadow now keys
    on the served input_id (app.py always serves input_id while waiting).

    Cases: a real submit captures the served input_id in the shadow record;
    a stale input_id with the SAME question text clears the shadow and the
    composer is editable again; the CURRENT input_id keeps the composer
    hidden while the answer processes; a legacy shadow record without
    input_id (only possible from the previous JS version in the same
    browser session) keeps the pre-change question-text behavior."""
    from playwright.sync_api import sync_playwright, expect
    root = tmp_path_factory.mktemp('crosshost-shadow')
    host = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, host.base)
            task_id = create_task(page, host.base, 'Surf school booking (shadow)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            expect(page.locator('#answerform')).to_be_visible(timeout=30000)

            def served():
                state = page.request.get(host.base + '/api/tasks/' + task_id).json()['input_state']
                assert state['waiting'] and state['input_id'] and state['question'], state
                return state

            key = 'shadow-answer:%s' % task_id
            first = served()
            # Real submit through the composer captures the served input_id.
            page.locator('#answerform textarea[name="answer"]').fill('Customers pick a slot and pay online.')
            page.locator('#answerform button[type="submit"]').click()
            expect(page.locator('#shadowcopy')).to_be_visible(timeout=10000)
            shadow = page.evaluate("JSON.parse(sessionStorage.getItem(%r))" % key)
            assert shadow['input_id'] == first['input_id'], shadow

            cur = served()  # the next waiting input, whatever the engine asked
            import json as _json

            def seed(record):
                page.evaluate("sessionStorage.setItem(%r, %r)" % (key, _json.dumps(record)))
                page.reload()
                page.wait_for_selector('#answerform', timeout=30000)

            # Stale input_id, SAME question text: shadow clears, composer editable.
            seed({'question': cur['question'], 'answer': 'x', 'input_id': 'stale-id',
                  'at': page.evaluate('Date.now()')})
            expect(page.locator('#shadowcopy')).to_be_hidden(timeout=10000)
            area = page.locator('#answerform textarea[name="answer"]')
            expect(area).to_be_visible(timeout=10000)
            assert not area.is_disabled()

            # Current input_id: answer still processing - shadow stays, composer hidden.
            seed({'question': cur['question'], 'answer': 'x', 'input_id': cur['input_id'],
                  'at': page.evaluate('Date.now()')})
            expect(page.locator('#shadowcopy')).to_be_visible(timeout=10000)
            expect(page.locator('#answerform textarea[name="answer"]')).to_be_hidden(timeout=10000)

            # Legacy record (no input_id): pre-change question-text behavior preserved.
            seed({'question': cur['question'], 'answer': 'x', 'at': page.evaluate('Date.now()')})
            expect(page.locator('#answerform textarea[name="answer"]')).to_be_hidden(timeout=10000)
            seed({'question': 'Some other question entirely?', 'answer': 'x',
                  'at': page.evaluate('Date.now()')})
            expect(page.locator('#answerform textarea[name="answer"]')).to_be_visible(timeout=10000)
            browser.close()
    finally:
        host.stop()


def test_admitted_host_runs_the_first_interview_turn_through_the_ui(dsn, keys, tmp_path_factory):
    from playwright.sync_api import sync_playwright, expect
    root = tmp_path_factory.mktemp('crosshost-first-turn')
    host = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, host.base)
            task_id = create_task(page, host.base, 'Surf school booking (first turn)',
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
            # The deterministic stub opens with this exact question
            # (tests/crosshost_stub/sitecustomize.py script).
            assert question == 'Walk me through one concrete use case, part 1?'
            page.locator('#answerform textarea[name="answer"]').fill('Customers pick a slot and pay online.')
            page.locator('#answerform button[type="submit"]').click()
            browser.close()
        # The turn landed in the placement registry: one session row, this owner.
        import psycopg
        import psycopg.rows
        conn = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
        rows = conn.execute("select session_id, owner_id::text, generation from engine_sessions where owner_id=%s and task_id=%s", (OWNER, task_id)).fetchall()
        conn.close()
        assert len(rows) == 1 and rows[0]['generation'] >= 1
    finally:
        host.stop()


def test_phase_boundary_second_instance_cannot_pick_up_committed_needs(dsn, keys, tmp_path_factory):
    """Reproduction, not acceptance: a committed phase boundary does NOT cross
    hosts today. Host A completes the interview and the needs phase; host B
    (own artifacts root, same database - the Mac/OVH shape) can see the task
    and the waiting gate but cannot review it: the needs evidence lives only
    on A's filesystem, nothing publishes committed artifact bytes to the
    shared store by default (ARTIFACT_PUBLISH is off), and no restore path
    materializes them on B. Approving the gate on B is refused with "No
    current valid artifact", so srs - the full-phase successor, no mid-request
    handoff involved - can never start there.

    The interview itself does cross hosts (its engine checkpoints to the
    database); generator phase artifacts have no equivalent seam. This test
    pins the failure mode as evidence for the restore-seam design."""
    from playwright.sync_api import sync_playwright
    import psycopg, psycopg.rows
    root = tmp_path_factory.mktemp('crosshost-phases')
    answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
               'Rainy days are automatically refunded.', 'Owners see a daily roster.']

    local = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local.base)
            task_id = create_task(page, local.base, 'Surf school booking (phase hop)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            shots = root / 'shots'
            shots.mkdir(exist_ok=True)
            drive_interview(page, local.base, task_id, answers, shots=shots)
            headers = {'content-type': 'application/json', 'origin': local.base}
            preview = page.request.post(local.base + '/api/tasks/%s/start-next-phase' % task_id,
                                        data=json.dumps({'confirm': False}), headers=headers)
            assert preview.ok and preview.json().get('can_start'), preview.json()
            started = page.request.post(local.base + '/api/tasks/%s/start-next-phase' % task_id,
                                        data=json.dumps({'confirm': True}), headers=headers)
            assert started.ok and started.json().get('status') == 'started', started.json()
            review_a = wait_gate_review(page, local.base, task_id, 'gate-needs')
            needs_docs = gate_documents(review_a)
            assert needs_docs.get('needs.json'), 'needs evidence committed on host A: %r' % (list(needs_docs),)
            task = page.request.get(local.base + '/api/tasks/' + task_id).json()
            phases = {x['phase_key']: x['state'] for x in task['phases']}
            assert phases['needs'] == 'awaiting_approval', phases
            browser.close()
    finally:
        local.stop()

    # Default configuration publishes nothing: no manifest rows exist at all.
    conn = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
    published = conn.execute("select count(*) as n from artifact_manifest_ready").fetchone()
    stored_bytes = conn.execute("select count(*) as n from artifact_bytes").fetchone()
    jobs = conn.execute("select kind, state from worker_jobs where task_id=%s order by created_at", (task_id,)).fetchall()
    conn.close()
    assert published['n'] == 0 and stored_bytes['n'] == 0, \
        'committed needs bytes never reached the shared store: %r %r' % (published, stored_bytes)
    generator_jobs = [(j['kind'], j['state']) for j in jobs if j['kind'] != 'phase:intent']
    assert generator_jobs == [('phase:needs', 'done')], jobs  # interview steps claim phase:intent jobs

    cloud = AdmittedHost('cloud', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud.base)
            page.goto(cloud.base + '/tasks/%s/view' % task_id)
            page.wait_for_timeout(3000)
            # The task and its waiting gate are visible on B (control state is
            # in the shared database), but the evidence is not.
            task = page.request.get(cloud.base + '/api/tasks/' + task_id).json()
            phases = {x['phase_key']: x['state'] for x in task['phases']}
            assert phases['needs'] == 'awaiting_approval', phases
            review_b = page.request.get(cloud.base + '/api/tasks/%s/gates/gate-needs/review' % task_id)
            assert review_b.ok, review_b.status
            data_b = review_b.json()
            # 'actionable' is revision currency + gate state, not evidence presence:
            # B shows no evidence documents, and the decide path is where missing
            # evidence is enforced (below).
            assert not gate_documents(data_b), 'B has no evidence documents without restore: %r' % (list(gate_documents(data_b)),)
            assert not gate_documents(data_b).get('needs.json'), 'needs.json bytes exist only on host A'
            # Approving anyway is refused: no valid artifact to review on B,
            # so the srs successor can never start there.
            decision = page.request.post(cloud.base + '/api/tasks/%s/gates/gate-needs/decide' % task_id,
                                         data=json.dumps({'action': 'approve'}),
                                         headers={'content-type': 'application/json', 'origin': cloud.base})
            assert decision.status == 400, (decision.status, decision.text()[:300])
            assert 'No current valid artifact' in decision.text(), decision.text()[:400]
            task = page.request.get(cloud.base + '/api/tasks/' + task_id).json()
            phases = {x['phase_key']: x['state'] for x in task['phases']}
            assert phases['srs'] == 'pending', phases
            browser.close()
    finally:
        cloud.stop()


PUBLISH_RESTORE_ENV = {'DELIVERY_ARTIFACT_PUBLISH': '1', 'DELIVERY_ARTIFACT_RESTORE': '1'}


def _manifest_state(dsn_value, task_id):
    import psycopg, psycopg.rows
    conn = psycopg.connect(dsn_value, autocommit=True, row_factory=psycopg.rows.dict_row)
    ready = conn.execute("select revision_id, revision_hash from artifact_manifest_ready where task_id=%s", (task_id,)).fetchall()
    rows = conn.execute("select phase_key, name, sha256, publication_state, writer_instance from task_artifacts where task_id=%s order by id", (task_id,)).fetchall()
    blobs = conn.execute("select sha256, byte_size from artifact_bytes").fetchall()
    jobs = conn.execute("select kind, state from worker_jobs where task_id=%s order by created_at", (task_id,)).fetchall()
    conn.close()
    return ready, rows, blobs, jobs


def test_phase_boundary_second_instance_picks_up_committed_needs(dsn, keys, tmp_path_factory):
    """Acceptance: completed-phase artifacts cross hosts as verified published bytes.

    Host A (own artifacts root) completes the interview and the needs phase through
    the real UI path with publication on. Host B (own root, same database, restore
    on) never ran anything: the gate review materializes the committed evidence
    from the published manifest, the founder approves there, and srs runs as a real
    child process on B over the restored inputs. B's srs publication sits next to
    A's needs revision (content-addressed, no single-writer assumption), and a
    restarted A restores B's output back while its identical local bytes stay
    untouched. Settled work is never regenerated."""
    from playwright.sync_api import sync_playwright
    import hashlib as _hashlib
    root = tmp_path_factory.mktemp('crosshost-pickup')
    answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
               'Rainy days are automatically refunded.', 'Owners see a daily roster.']
    headers = lambda base: {'content-type': 'application/json', 'origin': base}

    local = AdmittedHost('local', root, keys, dsn, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local.base)
            task_id = create_task(page, local.base, 'Surf school booking (pickup)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            shots = root / 'shots'
            shots.mkdir(exist_ok=True)
            drive_interview(page, local.base, task_id, answers, shots=shots)
            preview = page.request.post(local.base + '/api/tasks/%s/start-next-phase' % task_id,
                                        data=json.dumps({'confirm': False}), headers=headers(local.base))
            assert preview.ok and preview.json().get('can_start'), preview.json()
            started = page.request.post(local.base + '/api/tasks/%s/start-next-phase' % task_id,
                                        data=json.dumps({'confirm': True}), headers=headers(local.base))
            assert started.ok and started.json().get('status') == 'started', started.json()
            review_a = wait_gate_review(page, local.base, task_id, 'gate-needs')
            assert gate_documents(review_a).get('needs.json'), 'needs evidence committed on host A'
            browser.close()
    finally:
        local.stop()

    ready, rows, blobs, jobs = _manifest_state(dsn, task_id)
    assert len(ready) == 1, 'A published exactly one ready revision at the needs commit: %r' % (ready,)
    published = {(r['phase_key'], r['name']): r for r in rows if r['publication_state'] == 'published'}
    assert ('needs', 'needs.json') in published, published.keys()
    assert ('conops', 'conops.json') in published and ('intent', 'intent_model.json') in published, \
        'the committed interview artifacts publish with the same revision: %r' % (sorted(published),)
    needs_sha = published[('needs', 'needs.json')]['sha256']
    assert any(b['sha256'] == needs_sha for b in blobs), 'needs bytes are stored content-addressed'
    assert [(j['kind'], j['state']) for j in jobs if j['kind'] != 'phase:intent'] == [('phase:needs', 'done')], jobs

    cloud = AdmittedHost('cloud', root, keys, dsn, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud.base)
            task = page.request.get(cloud.base + '/api/tasks/' + task_id).json()
            assert {x['phase_key']: x['state'] for x in task['phases']}['needs'] == 'awaiting_approval'
            review_b = wait_gate_review(page, cloud.base, task_id, 'gate-needs', timeout=30)
            assert review_b.get('actionable'), 'B reviews the restored evidence: %r' % (str(review_b)[:400],)
            restored = cloud.artifacts / 'tenants' / OWNER / 'sessions' / str(task['engine_session_id']) / 'spec' / 'needs.json'
            assert restored.is_file(), 'restore materialized needs.json on B: %s' % restored
            assert _hashlib.sha256(restored.read_bytes()).hexdigest() == needs_sha, 'restored bytes match the published hash'
            decision = page.request.post(cloud.base + '/api/tasks/%s/gates/gate-needs/decide' % task_id,
                                         data=json.dumps({'action': 'approve', 'selected_option': 'Approve Stakeholder needs'}),
                                         headers=headers(cloud.base))
            assert decision.ok, (decision.status, decision.text()[:300])
            review_srs = wait_gate_review(page, cloud.base, task_id, 'gate-srs', timeout=180)
            assert gate_documents(review_srs).get('srs.json'), 'srs ran as a real child on B over restored inputs'
            browser.close()
    finally:
        cloud.stop()

    ready2, rows2, blobs2, jobs2 = _manifest_state(dsn, task_id)
    assert len(ready2) == 1 and ready2[0]['revision_id'] != ready[0]['revision_id'], \
        "B's srs commit published a new revision: %r -> %r" % (ready, ready2)
    published2 = {(r['phase_key'], r['name']): r for r in rows2 if r['publication_state'] == 'published'}
    assert ('srs', 'srs.json') in published2 and ('needs', 'needs.json') in published2, \
        "B's revision carries A's needs next to B's srs: %r" % (sorted(published2),)
    # writer_instance is hostname#pid and sequential hosts in one container can
    # reuse a pid; the producer identity proof is filesystem-based instead:
    session_id = task['engine_session_id']
    srs_on_a = local.artifacts / 'tenants' / OWNER / 'sessions' / str(session_id) / 'spec' / 'srs.json'
    assert not srs_on_a.exists(), 'srs was produced on B while A was down, not on A'
    assert [(j['kind'], j['state']) for j in jobs2 if j['kind'] != 'phase:intent'] == [('phase:needs', 'done'), ('phase:srs', 'done')], jobs2

    # The ping-pong: a restarted A restores B's published srs output; A's own
    # identical needs.json is a no-op, never overwritten.
    local2 = AdmittedHost('local2', root, keys, dsn, artifacts=local.artifacts, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local2.base)
            review_a2 = wait_gate_review(page, local2.base, task_id, 'gate-srs', timeout=30)
            assert review_a2.get('actionable'), 'A reviews what B produced: %r' % (str(review_a2)[:400],)
            restored_srs = local2.artifacts / 'tenants' / OWNER / 'sessions' / str(session_id) / 'spec' / 'srs.json'
            assert restored_srs.is_file(), 'A restored srs.json from B''s revision'
            assert _hashlib.sha256(restored_srs.read_bytes()).hexdigest() == published2[('srs', 'srs.json')]['sha256']
            assert _hashlib.sha256((local2.artifacts / 'tenants' / OWNER / 'sessions' / str(session_id) / 'spec' / 'needs.json').read_bytes()).hexdigest() == needs_sha
            browser.close()
    finally:
        local2.stop()


def test_full_session_alternates_hosts_through_validation(dsn, keys, tmp_path_factory):
    """Full-session cross-host acceptance, extending the needs pickup through every
    remaining phase family: each boundary crosses hosts as verified published
    bytes and settled work is never regenerated.

    A runs the interview + needs (real UI child). B restores, approves gate-needs,
    runs srs. A2 restores (semantic-review companion included), approves gate-srs
    on the NON-producer host, runs architecture with a research_required stack
    slot. B2 restores the founder-decision companion, records the founder stack
    decisions on the gate surface, approves gate-architecture on the NON-producer
    host, runs items over the restored companion. A3 (the pre-decision producer
    root) must REFUSE pickup: its local companion conflicts with the published
    founder-decision record and restore never overwrites conflicting local bytes
    (a pending founder amendment would fast-forward provably-published bytes).
    B3 restores, approves gate-specs, runs tests. B4 restores, approves gate-tests,
    runs validation, approves gate-validation: the session reaches a terminal state
    with every declared

    NOTE (2026-10-24): the earlier greenfield milestone (6f34d00) is RETRACTED as
    evidence of safe all-phase handoff: without the companion binding it masked a
    settled-work substitution - a pickup host's checkpoint-local resume regenerated
    the approved architecture downstream and published the substitute. The
    settled-substitution guard (artifact_publication.pin_settled_artifacts) now
    fails that class loudly before publication; the generator's checkpoint-local
    resume still needs its cause-level fix (disk-committed stages must bind to the
    authoritative snapshot) before this test can assert a clean terminal run.
    artifact - approval evidence companions included - published content-addressed."""
    from playwright.sync_api import sync_playwright
    import hashlib as _hashlib
    root = tmp_path_factory.mktemp('crosshost-full')
    answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
               'Rainy days are automatically refunded.', 'Owners see a daily roster.']
    headers = lambda base: {'content-type': 'application/json', 'origin': base}

    def decide(page, base, gate, option):
        decision = page.request.post(base + '/api/tasks/%s/gates/%s/decide' % (task_id, gate),
                                     data=json.dumps({'action': 'approve', 'selected_option': option}),
                                     headers=headers(base))
        assert decision.ok, (gate, decision.status, decision.text()[:300])

    def spec_file(host, session_id, name):
        return host.artifacts / 'tenants' / OWNER / 'sessions' / str(session_id) / 'spec' / name

    # Host A: interview + needs through the real UI child process.
    local = AdmittedHost('local', root, keys, dsn, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local.base)
            task_id = create_task(page, local.base, 'Surf school booking (full session)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            drive_interview(page, local.base, task_id, answers, shots=root / 'shots-a')
            started = page.request.post(local.base + '/api/tasks/%s/start-next-phase' % task_id,
                                        data=json.dumps({'confirm': True}), headers=headers(local.base))
            assert started.ok and started.json().get('status') == 'started', started.json()
            review_a = wait_gate_review(page, local.base, task_id, 'gate-needs')
            assert gate_documents(review_a).get('needs.json'), 'needs evidence committed on host A'
            browser.close()
    finally:
        local.stop()

    ready, rows, blobs, jobs = _manifest_state(dsn, task_id)
    published = {(r['phase_key'], r['name']): r for r in rows if r['publication_state'] == 'published'}
    needs_sha = published[('needs', 'needs.json')]['sha256']
    task = None

    # Host B: restore needs, approve, srs as a real child over restored inputs.
    cloud = AdmittedHost('cloud', root, keys, dsn, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud.base)
            task = page.request.get(cloud.base + '/api/tasks/' + task_id).json()
            session_id = task['engine_session_id']
            review_b = wait_gate_review(page, cloud.base, task_id, 'gate-needs', timeout=30)
            assert review_b.get('actionable'), 'B reviews the restored needs: %r' % (str(review_b)[:300],)
            restored = spec_file(cloud, session_id, 'needs.json')
            assert restored.is_file() and _hashlib.sha256(restored.read_bytes()).hexdigest() == needs_sha
            decide(page, cloud.base, 'gate-needs', 'Approve Stakeholder needs')
            review_srs = wait_gate_review(page, cloud.base, task_id, 'gate-srs', timeout=180)
            assert gate_documents(review_srs).get('srs.json'), 'srs ran as a real child on B'
            browser.close()
    finally:
        cloud.stop()

    _, rows2, _, _ = _manifest_state(dsn, task_id)
    published2 = {(r['phase_key'], r['name']): r for r in rows2 if r['publication_state'] == 'published'}
    srs_sha = published2[('srs', 'srs.json')]['sha256']

    # Host A2 (A's root): restore, approve gate-srs (HOST SWITCH at this gate: the
    # semantic-review companion must cross and the fail-closed check runs on A2),
    # then run architecture with a research_required stack slot.
    local2 = AdmittedHost('local2', root, keys, dsn, artifacts=local.artifacts,
                          extra_env=dict(PUBLISH_RESTORE_ENV, DELIVERY_TEST_STACK_MODE='research_required'))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local2.base)
            review_srs_a = wait_gate_review(page, local2.base, task_id, 'gate-srs', timeout=30)
            assert review_srs_a.get('actionable'), 'A2 reviews B\'s srs: %r' % (str(review_srs_a)[:300],)
            restored_srs = spec_file(local2, session_id, 'srs.json')
            assert restored_srs.is_file() and _hashlib.sha256(restored_srs.read_bytes()).hexdigest() == srs_sha
            restored_sem = spec_file(local2, session_id, 'srs.semantic-review.json')
            assert restored_sem.is_file(), 'the semantic-review companion crossed to A2 via the manifest'
            _, rows_sem, _, _ = _manifest_state(dsn, task_id)
            sem_row = next(r for r in rows_sem if r['name'] == 'srs.semantic-review.json' and r['publication_state'] == 'published')
            assert _hashlib.sha256(restored_sem.read_bytes()).hexdigest() == sem_row['sha256'], 'restored companion bytes match the published hash'
            decide(page, local2.base, 'gate-srs', 'Approve Requirements')
            review_arch = wait_gate_review(page, local2.base, task_id, 'gate-architecture', timeout=180)
            assert gate_documents(review_arch).get('architecture.json'), 'architecture ran as a real child on A2'
            stack_a = (review_arch.get('review') or {}).get('stack_decisions') or review_arch.get('stack_decisions')
            assert stack_a and [s for s in stack_a['slots'] if not s.get('founder_decision')], \
                'the research_required slot surfaces the founder-decision panel on the producer host: %r' % (str(review_arch)[:400],)
            browser.close()
    finally:
        local2.stop()

    _, rows3, _, _ = _manifest_state(dsn, task_id)
    published3 = {(r['phase_key'], r['name']): r for r in rows3 if r['publication_state'] == 'published'}
    assert ('architecture', 'architecture.json') in published3 and ('architecture', 'architecture.decisions.json') in published3, \
        'A2 published architecture and its founder-decision companion: %r' % (sorted(published3),)
    a2_companion_bytes = spec_file(local2, session_id, 'architecture.decisions.json').read_bytes()
    # Producer identity: B's root never saw architecture while B was down.
    assert not spec_file(cloud, session_id, 'architecture.json').exists(), 'architecture was produced on A2, not on B'

    # Host B2 (B's root): HOST SWITCH at gate-architecture - restore the
    # founder-decision companion from the published manifest, record the founder
    # decisions there, approve, and run items over the restored companion.
    cloud2 = AdmittedHost('cloud2', root, keys, dsn, artifacts=cloud.artifacts, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud2.base)
            review_arch_b = wait_gate_review(page, cloud2.base, task_id, 'gate-architecture', timeout=30)
            assert review_arch_b.get('actionable'), 'B2 reviews A2\'s architecture: %r' % (str(review_arch_b)[:300],)
            restored_companion = spec_file(cloud2, session_id, 'architecture.decisions.json')
            assert restored_companion.is_file(), 'the founder-decision companion crossed to B2 via the manifest'
            assert _hashlib.sha256(restored_companion.read_bytes()).hexdigest() == published3[('architecture', 'architecture.decisions.json')]['sha256'], \
                'restored companion bytes match the published hash'
            stack_b = (review_arch_b.get('review') or {}).get('stack_decisions') or review_arch_b.get('stack_decisions')
            # Slots carry the design proposal (technical_choice/prefill); the founder
            # overrides on the restored companion - the cross-host decision proof.
            open_b = [s['key'] for s in (stack_b or {}).get('slots', []) if not s.get('founder_decision')]
            assert open_b, 'B2 sees the founder-decision slots from the restored companion: %r' % (str(review_arch_b)[:400],)
            saved = page.request.post(cloud2.base + '/api/tasks/%s/gates/gate-architecture/stack-decisions' % task_id,
                                      data=json.dumps({'revision': stack_b['revision'],
                                                       'decisions': [{'key': key, 'text': 'python', 'reason': 'deterministic acceptance'} for key in open_b]}),
                                      headers=headers(cloud2.base))
            assert saved.ok and not saved.json().get('open'), (saved.status, saved.text()[:300])
            decide(page, cloud2.base, 'gate-architecture', 'Approve Architecture')
            review_specs = wait_gate_review(page, cloud2.base, task_id, 'gate-specs', timeout=180)
            docs_specs = gate_documents(review_specs)
            # Generator 0.28.0 emits items.json (+ specs/*.md); spec_items.json is a
            # legacy tolerated name, not a current output.
            assert docs_specs.get('items.json'), 'items ran as a real child on B2 over the restored companion: %r' % (sorted(docs_specs),)
            browser.close()
    finally:
        cloud2.stop()

    _, rows4, _, _ = _manifest_state(dsn, task_id)
    published4 = {(r['phase_key'], r['name']): r for r in rows4 if r['publication_state'] == 'published'}
    assert ('specs', 'items.json') in published4, sorted(published4)
    # Producer identity: A's root never saw items while A was down.
    assert not spec_file(local2, session_id, 'items.json').exists(), 'items were produced on B2, not on A'

    # Host A3 (A's root): the stale-replica boundary of the APPROVED restore
    # behavior. A2 produced the open-slots companion on this root; the founder's
    # decisions were recorded on B2 and republished, so A3's local companion is an
    # older committed render of the same document. Restore never overwrites
    # conflicting local bytes: the pickup refuses cleanly and recoverably, and
    # every local byte is preserved. (A pending founder amendment would let
    # provably-previously-published bytes fast-forward to the latest revision.)
    local3 = AdmittedHost('local3', root, keys, dsn, artifacts=local.artifacts, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local3.base)
            refused = page.request.get(local3.base + '/api/tasks/%s/gates/gate-specs/review' % task_id)
            assert refused.status == 409 and 'conflicting local bytes' in refused.text(), \
                'A3 holds the pre-decision companion: restore refuses without overwriting: %r' % ((refused.status, refused.text()[:200]),)
            browser.close()
    finally:
        local3.stop()
    stale_companion = spec_file(local3, session_id, 'architecture.decisions.json')
    assert stale_companion.is_file() and stale_companion.read_bytes() == a2_companion_bytes, \
        'the refusal left every local byte intact (conflicting local bytes are never overwritten)'

    # Host B3 (B's root): restore (the decided companion is identical on this
    # root), approve gate-specs, tests child.
    cloud3 = AdmittedHost('cloud3', root, keys, dsn, artifacts=cloud.artifacts, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud3.base)
            review_specs_b = wait_gate_review(page, cloud3.base, task_id, 'gate-specs', timeout=30)
            assert review_specs_b.get('actionable'), 'B3 reviews B2\'s items: %r' % (str(review_specs_b)[:300],)
            restored_items = spec_file(cloud3, session_id, 'items.json')
            assert restored_items.is_file() and \
                _hashlib.sha256(restored_items.read_bytes()).hexdigest() == published4[('specs', 'items.json')]['sha256']
            decide(page, cloud3.base, 'gate-specs', 'Approve Item specifications')
            review_tests = wait_gate_review(page, cloud3.base, task_id, 'gate-tests', timeout=180)
            assert gate_documents(review_tests).get('tests.json'), 'tests ran as a real child on B3'
            browser.close()
    finally:
        cloud3.stop()

    _, rows5, _, _ = _manifest_state(dsn, task_id)
    published5 = {(r['phase_key'], r['name']): r for r in rows5 if r['publication_state'] == 'published'}
    assert ('tests', 'tests.json') in published5, sorted(published5)
    assert not spec_file(local2, session_id, 'tests.json').exists(), 'tests were produced on B3, not on A'

    # Host B4 (B's root): restore, approve gate-tests, validation child, terminal approval.
    cloud4 = AdmittedHost('cloud4', root, keys, dsn, artifacts=cloud.artifacts, extra_env=dict(PUBLISH_RESTORE_ENV))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, cloud4.base)
            local3 = cloud4  # the shared tail below drives through `local3.base`
            review_tests_b = wait_gate_review(page, local3.base, task_id, 'gate-tests', timeout=30)
            assert review_tests_b.get('actionable'), 'B4 reviews B3\'s tests: %r' % (str(review_tests_b)[:300],)
            restored_tests = spec_file(local3, session_id, 'tests.json')
            assert restored_tests.is_file() and \
                _hashlib.sha256(restored_tests.read_bytes()).hexdigest() == published5[('tests', 'tests.json')]['sha256']
            decide(page, local3.base, 'gate-tests', 'Approve Verification')
            review_val = wait_gate_review(page, local3.base, task_id, 'gate-validation', timeout=180)
            assert gate_documents(review_val).get('validation.json'), 'validation ran as a real child on B4'
            decide(page, local3.base, 'gate-validation', 'Approve Validation')
            final = page.request.get(local3.base + '/api/tasks/' + task_id).json()
            states = {x['phase_key']: x['state'] for x in final['phases']}
            assert all(state == 'approved' for state in states.values()), 'terminal state: every phase approved: %r' % (states,)
            gate_states = {g['gate_key']: g['state'] for g in final['gates']}
            # Interview gates stay idle by design: intent/conops are approved inside
            # the completed interview, never via gate decisions.
            assert gate_states == {'gate-intent': 'idle', 'gate-conops': 'idle',
                                   'gate-needs': 'decided', 'gate-srs': 'decided',
                                   'gate-architecture': 'decided', 'gate-specs': 'decided',
                                   'gate-tests': 'decided', 'gate-validation': 'decided'}, gate_states
            browser.close()
    finally:
        local3.stop()

    ready_f, rows_f, blobs_f, jobs_f = _manifest_state(dsn, task_id)
    assert len(ready_f) == 1, 'one ready revision at the terminal commit: %r' % (ready_f,)
    expected = {('intent', 'intent_model.json'), ('conops', 'conops.json'), ('needs', 'needs.json'),
                ('srs', 'srs.json'), ('srs', 'srs.semantic-review.json'),
                ('architecture', 'architecture.json'), ('architecture', 'architecture.decisions.json'),
                ('specs', 'items.json'), ('tests', 'tests.json'), ('validation', 'validation.json')}
    published_f = {(r['phase_key'], r['name']) for r in rows_f if r['publication_state'] == 'published'}
    assert expected <= published_f, 'terminal revision carries every declared artifact: missing %r' % (expected - published_f,)
    for phase_key, name in expected:
        row = next(r for r in rows_f if r['publication_state'] == 'published' and r['phase_key'] == phase_key and r['name'] == name)
        assert any(b['sha256'] == row['sha256'] for b in blobs_f), '%s/%s bytes are stored content-addressed' % (phase_key, name)
    done_jobs = {j['kind'] for j in jobs_f if j['state'] == 'done'}
    for kind in ('phase:needs', 'phase:srs', 'phase:architecture', 'phase:tests', 'phase:validation'):
        assert kind in done_jobs, '%s completed: %r' % (kind, sorted(done_jobs))


def test_decision_page_recovers_when_the_post_accept_navigation_fetch_fails(dsn, keys, tmp_path_factory):
    """Injected-failure regression for the stranded-form repair: the decision
    page's submit() POSTs the answer, then fetches the task to choose its
    navigation. Abort every post-accept read. The repair must (1) never resubmit
    the committed answer, (2) retry the read with bounded backoff, then
    (3) navigate to the task view itself instead of stranding the disabled form.
    The pre-repair strand signature is pinned in git history (d8d668d)."""
    from playwright.sync_api import sync_playwright
    root = tmp_path_factory.mktemp('crosshost-strand')
    answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
               'Rainy days are automatically refunded.', 'Owners see a daily roster.']
    local = AdmittedHost('local', root, keys, dsn)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            sign_in(page, local.base)
            task_id = create_task(page, local.base, 'Surf school booking (strand)',
                                  'A SaaS web app for surf schools, with booking and customer logins')
            page.locator('#startengine').click()
            pump_interview(page, local.base, task_id, answers, finish=False)
            state = page.request.get(local.base + '/api/tasks/' + task_id).json().get('input_state') or {}
            assert state.get('waiting') and state.get('decision'), 'an open decision to commit: %r' % (state,)
            launcher = page.locator('#opendecision')
            page.wait_for_selector('#opendecision', state='visible', timeout=30000)
            launcher.click()
            page.wait_for_url('**/decisions/**', timeout=30000)
            page.wait_for_selector('#reviewform', timeout=30000)

            # Abort exactly the post-accept navigation fetch: the first GET of the
            # task JSON AFTER the decision POST has gone through. Background pollers
            # and the pre-submit recheck fall through untouched.
            from urllib.parse import urlsplit
            seen = {'posts': 0, 'aborted': 0}
            shots = root / 'strand-shots'
            shots.mkdir()
            def one_shot_abort(route):
                path = urlsplit(route.request.url).path.rstrip('/')
                if route.request.method == 'POST' and path.endswith('/answer'):
                    seen['posts'] += 1
                    route.fallback()
                    return
                if (route.request.method == 'GET' and seen['posts']
                        and path == '/api/tasks/%s' % task_id):
                    # Keep aborting: any poller may interleave with the real
                    # navigation fetch; the page must never get the post-accept read.
                    seen['aborted'] = seen.get('aborted', 0) + 1
                    route.abort('connectionreset')
                    return
                route.fallback()
            import json as _json
            page.on('console', lambda m: seen.setdefault('console', []).append(m.text[:160]) if m.type in ('warning', 'error') else None)
            page.route('**/api/tasks/%s**' % task_id, one_shot_abort)  # ** suffix: any query string

            radios = page.locator('input[type="radio"]')
            if radios.count():
                radios.first.check()
                rationale = page.locator('textarea[name="rationale"]')
                if rationale.count() and rationale.is_visible():
                    rationale.fill('Deterministic acceptance: first option fits.')
            clicked = False
            for label in ('Choose and continue', 'Proceed', 'Approve', 'Continue'):
                button = page.get_by_role('button', name=label, exact=True)
                if button.count() and button.first.is_enabled():
                    button.first.click()
                    clicked = True
                    break
            assert clicked, 'a live decision button was clicked'
            page.wait_for_selector('#review-status', timeout=15000)
            page.wait_for_timeout(1200)

            assert seen['posts'] == 1, 'exactly one decision POST, no resubmission: %r' % (seen,)
            assert seen['aborted'], 'the navigation fetch was aborted: %r url=%s' % (seen, page.url)
            status_loc = page.locator('#review-status')
            status = status_loc.inner_text()
            assert 'Decision saved' in status and ('Reconnecting' in status or 'live view is unreachable' in status), \
                'the recovery status replaces the strand signature: %r' % (status,)
            # Viewport capture of the status area itself: full_page captures misplace
            # sticky chrome, so prove the status is on-screen where the founder looks.
            status_loc.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            box = status_loc.bounding_box()
            assert box and 0 <= box['y'] and box['y'] + box['height'] <= page.viewport_size['height'], \
                'the recovery status is fully in viewport: %r vs %r' % (box, page.viewport_size)
            page.screenshot(path=str(shots / 'recovery-status.png'))
            # The committed decision advanced the engine.
            after = page.request.get(local.base + '/api/tasks/' + task_id).json().get('input_state') or {}
            assert after.get('input_id') != state.get('input_id'), 'the committed decision advanced the engine'
            # Bounded retries all fail under the persistent abort; the page then
            # navigates itself to the task view instead of stranding the form.
            page.wait_for_url('**/tasks/*/view**', timeout=45000)
            assert seen['posts'] == 1, 'still no duplicate POST after the retry loop: %r' % (seen,)
            # Full recovery: lift the fault and the task view renders the live state.
            page.unroute('**/api/tasks/%s**' % task_id)
            page.reload()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(shots / 'recovered-task-view.png'), full_page=True)
            live = page.locator('#opendecision, #answerform, #startneeds')
            assert live.count() and live.first.is_visible(), \
                'the task view renders the live state after recovery: %s' % (page.url,)
            browser.close()
    finally:
        local.stop()
