"""Deterministic cross-host acceptance stub, injected via PYTHONPATH.

Active only when CROSSHOST_STUB=1. Runs at interpreter startup inside the test
server processes (delivery app and, for the local host, the separate engine),
so every instance - local or cloud, any process - answers model calls from the
same deterministic script and verifies test-minted tokens. No provider HTTP
call can happen: the chat-model factory is replaced wholesale, and the Ollama
model-listing preflight is answered from the stub.

Nothing here is imported by production code; production behavior is unchanged
without the environment opt-in.

Lesson (2026-10-23 escaped-stub incident): a standalone diagnostic that set
env vars after interpreter start, without this stub, dispatched real HTTPS
request(s) to DeepSeek with a dummy key and got real 401s. Diagnostics and
repros must run under this stub (or an equivalent fail-closed guard), never
on expected-to-fail credentials: an expected failure still sends the request.
"""
import os

if os.environ.get('CROSSHOST_STUB') == '1':
    # Fail-closed egress guard: only loopback TCP and Unix-domain sockets
    # (Postgres) are reachable from a stubbed process. Patch below every HTTP
    # client: raw connect/connect_ex (callers can bypass create_connection),
    # the create_connection helper (urllib/http.client/httpcore sync), and
    # getaddrinfo (async clients resolve before sock.connect through the
    # event loop), so async paths are covered by the same two seams.
    import socket as _socket

    def _allowed(host):
        return isinstance(host, str) and (host.startswith('127.') or host in ('::1', 'localhost'))

    class _EgressBlocked(OSError):
        pass

    def _guard_host(host):
        if not _allowed(host):
            raise _EgressBlocked('crosshost egress guard: non-loopback destination %r refused' % (host,))

    _real_connect = _socket.socket.connect
    _real_connect_ex = _socket.socket.connect_ex
    _real_create_connection = _socket.create_connection
    _real_getaddrinfo = _socket.getaddrinfo

    def _connect(sock, address):
        if isinstance(address, tuple) and address:
            _guard_host(address[0])
        return _real_connect(sock, address)

    def _connect_ex(sock, address):
        if isinstance(address, tuple) and address:
            _guard_host(address[0])
        return _real_connect_ex(sock, address)

    def _create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and address:
            _guard_host(address[0])
        return _real_create_connection(address, *args, **kwargs)

    def _getaddrinfo(host, *args, **kwargs):
        _guard_host(host)
        return _real_getaddrinfo(host, *args, **kwargs)

    _socket.socket.connect = _connect
    _socket.socket.connect_ex = _connect_ex
    _socket.create_connection = _create_connection
    _socket.getaddrinfo = _getaddrinfo

    # Self-check, fail-closed at startup: external-IP and hostname attempts
    # must fail before any connect, loopback must keep working.
    try:
        _socket.create_connection(('93.184.216.34', 443), timeout=1)
        raise SystemExit('crosshost egress guard self-check FAILED: external IP connected')
    except _EgressBlocked:
        pass
    try:
        _socket.getaddrinfo('api.deepseek.com', 443)
        raise SystemExit('crosshost egress guard self-check FAILED: hostname resolved')
    except _EgressBlocked:
        pass
    _socket.getaddrinfo('127.0.0.1', 1)

    import json

    from langchain_core.messages import AIMessage

    from interview_engine import model_config

    _NEEDS = {'stakeholder_needs': [{'need_id': 'SN-1', 'statement': 'founders need specs', 'kind': 'functional', 'source_turns': [0]}]}
    _SCOPE = {'problem_statement': 'p', 'target_users': ['u'], 'core_value_proposition': 'v',
              'in_scope': ['a'], 'out_of_scope': ['b'], 'source_turns': {}}
    _CONOPS = [{'operating_environment': 'phone, often offline'}, {'trigger': 'habit completed'},
               {'operational_flow': ['open product', 'record points']}, {'exceptions': ['offline']},
               {'name': 'Native app', 'summary': 'installed app'}, {'name': 'PWA', 'summary': 'installable web product'},
               {'advantages': ['native APIs']}, {'disadvantages': ['store release']},
               {'advantages': ['one deployment']}, {'disadvantages': ['platform limits']}]
    _TIEBREAK = {'is_web_app': True, 'archetype': 'saas_crud', 'confidence': 0.9, 'reason': 'web app'}
    _QUESTIONS = int(os.environ.get('CROSSHOST_STUB_QUESTIONS', '3'))

    class _Fake:
        """One scripted reply sequence per stage, like the repo's in-process fakes.

        Probing replies are addressed by conversation position - the count of
        AI messages already in the supplied history - not by call order: each
        engine turn appends exactly one AI message (the asked question) to the
        checkpointed transcript, and the probing node passes that full
        transcript (graph.py: request_messages = [SystemMessage, *history,
        HumanMessage(contract)]). A real model infers its position from the
        same history; a pop-order queue would restart at the first reply on
        every host restart and pin the interview (coverage never advances on
        the reclaiming host - observed 2026-10-23 as the cross-host stall).
        Other stages (distillation/conops/tiebreak) do not thread their
        outputs into the message list, so position cannot be recovered from
        the supplied messages; they keep call order - their multi-call
        sequences run inside one graph node, and langgraph resume re-executes
        a node from its start, so a restart replays the same call sequence.
        Residual harness limitation: the categorization consumer shares stage
        'probing' but sends history-less calls, so it always derives envelope
        0; the acceptance path never exercises it (no categorization model
        call is made for the scripted answers), and a future graph that
        checkpoints inside a multi-call stage or threads other stages into
        the transcript invalidates the call-order assumption above."""
        def __init__(self, stage, replies):
            self.stage = stage
            self.replies = list(replies)

        def invoke(self, messages):
            if self.stage == 'probing' and self.replies:
                prior = sum(1 for m in messages if getattr(m, 'type', '') == 'ai')
                content = self.replies[min(prior, len(self.replies) - 1)]
            else:
                content = self.replies.pop(0) if self.replies else '[INTERVIEW COMPLETE]'
            return AIMessage(content=content if isinstance(content, str) else json.dumps(content))

    def _script(stage):
        if stage == 'probing':
            # Saturate deterministically: coverage completion is code-owned in
            # the engine (next_area(coverage) is None), so each scripted reply
            # self-reports a chunk of the area map and the final scripted reply
            # closes the interview. The founder's closing answer then takes the
            # engine's pending_closing branch to [INTERVIEW COMPLETE].
            from interview_engine.coverage_taxonomy import AREA_KEYS
            chunks = [sorted(AREA_KEYS)[i::_QUESTIONS] for i in range(_QUESTIONS)]
            envelopes = [{'question': 'Walk me through one concrete use case, part %d?' % (i + 1),
                          'covered': chunk, 'thin': [], 'skipped': [], 'complete': False}
                         for i, chunk in enumerate(chunks)]
            envelopes.append({'question': 'Anything else to add before we wrap up?',
                              'covered': [], 'thin': [], 'skipped': [], 'complete': True})
            return envelopes
        if stage == 'distillation':
            return [_NEEDS, _SCOPE, []]
        if stage == 'conops':
            return list(_CONOPS)
        if stage == 'scope_tiebreak':
            return [_TIEBREAK] * 4
        return ['[INTERVIEW COMPLETE]']

    _box = {}

    def _build(stage, **kwargs):
        # Cache per stage like the graph does: one model, one reply queue.
        if stage not in _box:
            _box[stage] = _Fake(stage, _script(stage))
        # Register into the current job binding exactly like the real factory,
        # so the worker-stage wire check (wire_model) still runs against the
        # session's pinned provider/model.
        from interview_engine import model_config as _mc
        from interview_engine.wire_binding import register_model
        cfg = _mc.for_stage(stage)
        return register_model(_box[stage], stage=stage, provider=cfg.provider, model=cfg.model)

    model_config.build_chat_model = _build

    # --- spec_generator: deterministic schema-routed stage models ---
    # The generator runs in a worker thread of the same process (in-process
    # engine), so this interpreter-startup patch covers it. The factory is
    # replaced wholesale - no provider HTTP call can happen - and every fake
    # registers into the current job wire binding exactly like the real
    # factory, so the worker-stage wire check (wire_model) still runs against
    # the phase's pinned provider/model. Answers are routed by the per-call
    # SCHEMA (ADR-0022 micro-calls), mirroring the generator's own test fakes
    # (test_per_item_resume._routing): needs is code-carried 1:1 from the
    # intent model (no model call at all), srs is an overview call plus one
    # fill per skeleton slot, architecture is the micro-contract router, items
    # is a plan call plus one body per item, tests fills the required ids,
    # validation/review answer their stage payloads.
    try:
        import itertools as _itertools

        from spec_generator import model_config as _gmc
        from spec_generator.wire_binding import register_model as _gregister

        _G_PLAN = [{'slug': 'api', 'kind': 'api_surface', 'title': 'API',
                    'requirement_ids': ['R-1'], 'depends_on': []},
                   {'slug': 'web', 'kind': 'ui_flow', 'title': 'Web',
                    'requirement_ids': ['R-1'], 'depends_on': ['api']}]
        _G_TEST_FILL = {'description': 'criterion holds', 'components': ['api'], 'runner': 'pytest',
                        'definition': {'preconditions': [], 'steps': ['Run x'], 'expected_outcomes': ['passes'],
                                       'cleanup': [], 'cases': [], 'execution': {}}}
        _G_BODY_SEQ = _itertools.count(1)

        def _g_arch(schema):
            """0.13.0 micro-pipeline: answer the one small call this schema shapes."""
            props = set(schema.get('properties', {}))
            if props == {'stack_keys'}:
                return {'stack_keys': ['language']}
            if props == {'technology', 'off_list_justification', 'baseline_refs'}:
                return {'technology': 'python', 'off_list_justification': None,
                        'baseline_refs': ['runtime:spec-generator:1']}
            if props == {'responsibility'}:
                return {'responsibility': 'serve'}
            if props == {'position'}:
                return {'position': 'fast'}
            if 'driver' in props:
                return {'title': 't', 'decision': 'x', 'rationale': 'y', 'driver': 'simplicity',
                        'options_considered': [{'option': 'z', 'reason': 'heavier'}],
                        'baseline_refs': ['repo:spec-generator'], 'evidence': [],
                        'affected_repositories': [], 'executes_concept_ids': []}
            if schema.get('type') == 'array':
                item_props = set(schema['items'].get('properties', {}))
                if 'slug' in item_props:
                    return [{'slug': 't'}]
                return [{'name': 'api', 'requirement_ids': ['R-1'],
                         'baseline_refs': ['repo:spec-generator'], 'migration': False}]
            raise AssertionError('crosshost stub: unexpected architecture schema %r' % (schema,))

        def _g_answer(stage, schema):
            if stage == 'srs':
                if set(schema.get('properties', {})) == {'purpose', 'scope_summary'}:
                    return {'purpose': 'Deterministic acceptance purpose.', 'scope_summary': 'Acceptance scope.'}
                return {'title': 'Requirement title', 'statement': 'The system shall meet the need.',
                        'priority': 'must', 'acceptance': ['The criterion holds.']}
            if stage == 'items':
                if schema.get('type') == 'array':
                    return _G_PLAN
                n = next(_G_BODY_SEQ)
                return {'body_markdown': '# body %d\n\nModule %d owns its own distinct contract: input shape %d, error case %d.' % (n, n, n * 7, n * 13)}
            if stage == 'tests':
                wanted = schema['properties']['fills']['required']
                return {'fills': {tid: dict(_G_TEST_FILL, description='%s holds' % tid) for tid in wanted}}
            if stage == 'architecture':
                return _g_arch(schema)
            if stage == 'needs':
                return {'session_id': 's', 'needs': [{'need_id': 'N-1', 'stakeholder': 'founder',
                        'statement': 'needs specs', 'kind': 'functional',
                        'intent_fields': ['problem_statement'], 'intent_need_ids': ['SN-1']}]}
            if stage == 'validation':
                return {'items': []}
            if stage == 'review':
                return []
            return 'deterministic crosshost stub reply'

        class _GenFake:
            def __init__(self, stage, schema):
                self.stage = stage
                self.schema = schema or {}

            def _out(self):
                out = _g_answer(self.stage, self.schema)
                return out if isinstance(out, str) else json.dumps(out)

            def invoke(self, messages):
                return AIMessage(content=self._out())

            def stream(self, messages):
                text = self._out()
                step = max(1, len(text) // 3)
                for i in range(0, len(text), step):
                    yield AIMessage(content=text[i:i + step])

        def _g_build(stage, **kwargs):
            cfg = _gmc.for_stage(stage)
            return _gregister(_GenFake(stage, kwargs.get('schema')), stage=stage,
                              provider=cfg.provider, model=cfg.model)

        _gmc.build_chat_model = _g_build
    except ImportError:
        pass  # engine-only processes never import spec_generator

    # The Ollama model-listing preflight is the only plain httpx.get in the
    # startup path; answer it from the stub so no local Ollama is needed.
    import httpx

    _real_get = httpx.get

    class _Models:
        def raise_for_status(self):
            pass

        def json(self):
            return {'data': [{'id': 'qwen2.5-coder:14b'}, {'id': 'phi4:latest'}]}

    def _get(url, *args, **kwargs):
        if isinstance(url, str) and url.rstrip('/').endswith('/v1/models'):
            return _Models()
        return _real_get(url, *args, **kwargs)

    httpx.get = _get

    # Cloud instances verify founder tokens against a test key instead of a
    # Supabase JWKS endpoint: the acceptance test mints its own ES256 tokens.
    _jwks_file = os.environ.get('CROSSHOST_STUB_JWKS')
    if _jwks_file:
        try:
            from delivery_api import supabase_auth
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            class _Keys:
                def get_signing_key_from_jwt(self, token):
                    from types import SimpleNamespace
                    return SimpleNamespace(key=load_pem_public_key(open(_jwks_file, 'rb').read()))

            def _init(self, project_url, *, key_provider=None):
                # Bypass the real init: its strict <ref>.supabase.co hostname
                # check and live JWKS client cannot apply to the hermetic
                # crosshost URL. Set the same attributes the real init would.
                self.issuer = project_url.rstrip('/') + '/auth/v1'
                self.keys = key_provider or _Keys()

            supabase_auth.SupabaseIdentityVerifier.__init__ = _init
        except ImportError:
            pass  # engine-only processes never import delivery_api

    # Hermetic Supabase password sign-in: the real UI login flow posts to the
    # app's own /auth routes; the app's call to the Supabase auth API is the
    # single patched seam. The minted access token verifies through the JWKS
    # stub above, so cookies, refresh and tenant scope all exercise real code.
    if os.environ.get('CROSSHOST_STUB_AUTH') == '1':
        try:
            import time as _time
            import jwt as _jwt
            from delivery_api import supabase_sessions

            _key = open(os.environ['CROSSHOST_STUB_JWT_KEY'], 'rb').read()
            _owner = os.environ['CROSSHOST_STUB_OWNER']
            _issuer = os.environ['CROSSHOST_STUB_ISSUER']
            _session_id = os.environ.get('CROSSHOST_STUB_SESSION', _owner)

            def _payload():
                now = int(_time.time())
                token = _jwt.encode(
                    {'sub': _owner, 'session_id': _session_id, 'role': 'authenticated',
                     'aud': 'authenticated', 'iss': _issuer, 'iat': now, 'exp': now + 3600,
                     'email': 'founder@crosshost.test', 'user_metadata': {'email_verified': True}},
                    _key, algorithm='ES256', headers={'kid': 'crosshost-stub'})
                return {'access_token': token, 'refresh_token': 'crosshost-stub-refresh',
                        'expires_in': 3600, 'expires_at': now + 3600}

            def _call(path, payload, *, access=None):
                if path.startswith('token?grant_type=password') or path.startswith('token?grant_type=refresh_token'):
                    return _payload()
                if path.startswith('signup'):
                    return _payload()
                if path.startswith('logout'):
                    return {}
                raise AssertionError('crosshost stub: unexpected Supabase auth call ' + path)

            supabase_sessions._call = _call
        except ImportError:
            pass  # engine-only processes never import delivery_api
