"""Hermetic preflight tests for scripts/ops/preview-exec.sh.

The launcher must fail closed with a clear ABORTED code for every missing
prerequisite, before anything is started or written. docker and git are stubbed
on PATH; no container, network, or real checkout is involved. The provider key
must never be written to any file or printed.
"""
import os, stat, subprocess, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts' / 'ops' / 'preview-exec.sh'
LIB = REPO / 'scripts' / 'ops' / 'lib.sh'
KEY = 'sk-testkey-0123456789abcdef'

STAGE_MODEL_CONFIG = """from collections import namedtuple
StageModel = namedtuple('StageModel', 'provider model max_tokens')
_STAGE_DEFAULTS = {
    'main': StageModel(provider='ollama', model='phi4', max_tokens=4096),
}
"""


def _stubbin(tmp_path):
    d = tmp_path / 'bin'
    if d.exists():
        return d
    d.mkdir()
    (d / 'docker').write_text('#!/usr/bin/env bash\ncase "$1" in info) exit 0;; ps) exit 0;; *) exit 0;; esac\n')
    (d / 'git').write_text('''#!/usr/bin/env bash
# Answers every git question ops_start/candidate_tag asks, always clean main at origin.
while [ $# -gt 0 ]; do case "$1" in -C) shift 2;; *) break;; esac; done
case "$1" in
  remote) echo https://github.com/ShyAlon/delivery-api;;
  fetch|pull|checkout) exit 0;;
  status) exit 0;;
  rev-list) echo 0;;
  diff) exit 0;;
  rev-parse) case "$2" in --abbrev-ref) echo main;; --short=8) echo 00000000;; *) echo 0000000000000000000000000000000000000000;; esac;;
esac
exit 0
''')
    for tool in ('curl', 'shasum', 'tar'):
        (d / tool).write_text('#!/usr/bin/env bash\nexit 0\n')
    for f in d.iterdir():
        f.chmod(0o755)
    return d

def _settings(tmp_path, mode=0o600, body='DELIVERY_DATABASE_URL=postgresql://x/db\nDELIVERY_SUPABASE_URL=https://x\nDELIVERY_SUPABASE_ANON_KEY=k\n'):
    f = tmp_path / 'preview.env'
    f.write_text(body)
    f.chmod(mode)
    return f

def _plant_source_trees(root):
    """Minimal engine/generator source trees the launcher's source smoke reads
    (parse step, ast engine-surface check, pin-local stage caps)."""
    eng = root / 'interview-engine' / 'src' / 'interview_engine'
    eng.mkdir(parents=True, exist_ok=True)
    (eng / '__init__.py').write_text('')
    (eng / 'model_config.py').write_text(STAGE_MODEL_CONFIG)
    (eng / 'http_service.py').write_text(
        "SESSIONS_DSN_ENV = 'INTERVIEW_ENGINE_SESSIONS_DSN'\n\ndef create_app():\n    return None\n")
    (eng / 'pg_pool.py').write_text(
        "ENGINE_POOL_MAX = 4\n\ndef build_pool(dsn, *, max_size=4, name='engine'):\n    return None\n")
    gen = root / 'spec-generator' / 'src' / 'spec_generator'
    gen.mkdir(parents=True, exist_ok=True)
    (gen / '__init__.py').write_text('')
    (gen / 'model_config.py').write_text(STAGE_MODEL_CONFIG)


def _run(tmp_path, env_extra, settings=None, tty_input='yes\n'):
    root = tmp_path / 'repos'
    for r in ('delivery-api', 'interview-engine', 'spec-generator', 'verification-loop'):
        (root / r / '.git').mkdir(parents=True, exist_ok=True)
    _plant_source_trees(root)
    env = {'PATH': '%s:/usr/bin:/bin' % _stubbin(tmp_path), 'HOME': str(tmp_path),
           'REQUI_REPOS_ROOT': str(root)}
    env.update(env_extra)
    if settings is not None:
        env['PREVIEW_SETTINGS_FILE'] = str(settings)
    proc = subprocess.run(['bash', str(SCRIPT)], input=tty_input, capture_output=True, text=True,
                          cwd=REPO, env=env, timeout=30)
    return proc

def test_missing_key_aborts_before_anything(tmp_path):
    p = _run(tmp_path, {'PREVIEW_EXEC_PROVIDER': 'deepseek'}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-400:]
    assert p.returncode == 1

def test_short_or_spaced_key_aborts(tmp_path):
    for bad in ('sk-short', 'sk-has space init'):
        p = _run(tmp_path, {'DEEPSEEK_API_KEY': bad, 'PREVIEW_EXEC_PROVIDER': 'deepseek'}, settings=_settings(tmp_path))
        assert 'ABORTED [KEY-' in p.stdout, (bad, p.stdout[-300:])

def test_deepseek_without_an_exported_key_aborts_loudly_with_no_prompt(tmp_path):
    """2026-10-27 approved drop: the hidden key prompt is gone. The key must be
    exported in the shell that runs the preview; a missing key is a loud NO-KEY
    immediately - no prompt is shown and no line is read for a key."""
    tty = tmp_path / 'tty'
    tty.write_text(KEY + '\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'deepseek'},
             settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-500:]
    assert 'Type or paste it here' not in p.stdout
    assert KEY not in p.stdout and KEY not in p.stderr, 'the key must never be echoed or printed'

def test_the_script_has_no_hidden_key_read_at_all():
    """The approved drop, at the source: no `read -rs`, no key prompt text -
    a key typed into a terminal can end up in scrollback, so the script must
    never ask for one."""
    src = SCRIPT.read_text()
    assert 'read -rs' not in src
    assert 'Type or paste it here' not in src

def test_deepseek_only_mode_carries_no_ollama_settings():
    """DeepSeek-only means no Ollama dependency: the settings handed to the
    container must contain no OLLAMA_* and no DELIVERY_LOCAL_PREVIEW line,
    and the mode must abort OLLAMA-LEAK if one ever appears. Asserted on the
    source: the Ollama writes live in lib.sh's preview_ollama_env, the
    launcher's only call to it sits inside the ollama branch, and the guard
    runs in the deepseek branch after the settings file is built."""
    src = SCRIPT.read_text()
    lib = LIB.read_text()
    call = 'preview_ollama_env "$WORK/preview.env" "$OLLAMA_MODEL" "$OLLAMA_HOST"'
    assert src.count(call) == 1, 'preview_ollama_env is called exactly once'
    ollama_write = src.split('if [ "$PROVIDER" = ollama ]; then\n  ' + call, 1)
    assert len(ollama_write) == 2, 'the only Ollama settings write path must stay inside the ollama branch'
    assert 'OLLAMA_BASE_URL=' not in src, 'the launcher writes no Ollama settings itself'
    fn = lib.split('preview_ollama_env(){', 1)[1].split('\n}', 1)[0]
    assert 'OLLAMA_BASE_URL=' in fn and 'DELIVERY_OLLAMA_MODEL=' in fn, \
        'preview_ollama_env must write the host AND the reviewed model the pin prices'
    guard_block = src.split('if [ "$PROVIDER" = deepseek ]; then\n  grep -q -E', 1)
    assert len(guard_block) == 2 and 'OLLAMA-LEAK' in guard_block[1].split('fi', 1)[0], \
        'the no-Ollama guard must run in the deepseek branch'
    assert src.index('MODEL-KEY') < src.index('OLLAMA-LEAK'), 'the guard runs after the env file is built'


def test_unprovisioned_settings_abort(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY, 'PREVIEW_EXEC_PROVIDER': 'ollama'}, settings=tmp_path / 'absent.env')
    assert 'ABORTED [NOT-PROVISIONED]' in p.stdout

def test_settings_mode_must_be_600(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY, 'PREVIEW_EXEC_PROVIDER': 'ollama'}, settings=_settings(tmp_path, mode=0o644))
    assert 'ABORTED [SETTINGS-PERMS]' in p.stdout

def test_settings_without_database_aborts(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY, 'PREVIEW_EXEC_PROVIDER': 'ollama'}, settings=_settings(tmp_path, body='DELIVERY_SUPABASE_URL=https://x\n'))
    assert 'ABORTED [SETTINGS]' in p.stdout

def test_declined_confirmation_changes_nothing(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY, 'PREVIEW_EXEC_PROVIDER': 'ollama'}, settings=_settings(tmp_path), tty_input='no\n')
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout

def test_confirmed_start_passes_origin_checks(tmp_path):
    """Regression: the ORIGIN checks must read the built env FILE. Both greps
    once omitted the file argument and read the script's stdin instead, so
    every confirmed start - either provider mode - hung on a terminal or
    aborted ORIGIN. Reaching the pin step (a PIN abort: no Ollama server
    listens in this hermetic run) proves the checks passed against the file."""
    tty = tmp_path / 'tty'
    tty.write_text('yes\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama',
                        'OLLAMA_HOST': 'http://127.0.0.1:9'},
             settings=_settings(tmp_path))
    assert 'ABORTED [ORIGIN]' not in p.stdout, p.stdout[-500:]
    assert 'ABORTED [PIN' in p.stdout, p.stdout[-500:]

def test_provider_prompt_pick_ollama(tmp_path):
    """Provider unset: the script asks 1) ollama 2) deepseek. Picking 1 runs
    the ollama mode; the shared prompt handle then reaches EOF at the typed
    yes, which refuses. There is no default provider any more."""
    tty = tmp_path / 'tty'
    tty.write_text('1\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'OLLAMA_HOST': 'http://127.0.0.1:9'},
             settings=_settings(tmp_path))
    assert 'Provider for this preview: 1) ollama' in p.stdout, p.stdout[-500:]
    assert 'Provider: local Ollama model' in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NO-TERMINAL]' not in p.stdout and 'ABORTED [PROVIDER]' not in p.stdout

def test_provider_prompt_pick_deepseek_with_exported_key(tmp_path):
    """Picking 2 runs the deepseek mode; the exported key passes the loud
    presence check (no key prompt any more) and the flow stops at the typed
    yes (EOF refuses)."""
    tty = tmp_path / 'tty'
    tty.write_text('2\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'DEEPSEEK_API_KEY': KEY}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' not in p.stdout, p.stdout[-500:]
    assert 'Provider: DeepSeek only (paid)' in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout, p.stdout[-500:]
    assert 'Type or paste it here' not in p.stdout
    assert KEY not in p.stdout and KEY not in p.stderr, 'the key must never be echoed or printed'

def test_provider_prompt_pick_deepseek_without_a_key_aborts(tmp_path):
    """Picking 2 with no exported key stops loud with NO-KEY right after the
    pick - before the plan echo, before anything is built or started."""
    tty = tmp_path / 'tty'
    tty.write_text('2\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty)}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-400:]
    assert 'Type or paste it here' not in p.stdout
    assert p.returncode == 1

def test_provider_prompt_invalid_input_reasks(tmp_path):
    """An answer that is not 1 or 2 (including a bare Enter) is rejected and
    re-asked; the pick succeeds on a later valid line."""
    tty = tmp_path / 'tty'
    tty.write_text('\njunk\n1\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'OLLAMA_HOST': 'http://127.0.0.1:9'},
             settings=_settings(tmp_path))
    assert "not 1 or 2" in p.stdout, p.stdout[-500:]
    assert 'Provider: local Ollama model' in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout, p.stdout[-500:]

def test_provider_prompt_eof_fails_fast(tmp_path):
    """A readable but exhausted prompt stream (scripted run that forgot the
    pick) stops with a clear NO-TERMINAL, not a loop or a silent default."""
    tty = tmp_path / 'tty'
    tty.write_text('')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty)}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-TERMINAL]' in p.stdout, p.stdout[-400:]
    assert p.returncode == 1

def test_provider_unset_without_terminal_fails_fast(tmp_path):
    """Non-interactive with no PREVIEW_EXEC_PROVIDER: clear NO-TERMINAL naming
    the variable to set - never a silent provider choice."""
    p = _run(tmp_path, {}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-TERMINAL]' in p.stdout, p.stdout[-400:]
    assert 'PREVIEW_EXEC_PROVIDER' in p.stdout
    assert p.returncode == 1

def test_provider_env_skips_the_prompt(tmp_path):
    """PREVIEW_EXEC_PROVIDER set: no prompt is shown and the prompt stream is
    not consumed by a pick - its first line feeds the typed yes."""
    tty = tmp_path / 'tty'
    tty.write_text('yes\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama',
                        'OLLAMA_HOST': 'http://127.0.0.1:9'},
             settings=_settings(tmp_path))
    assert 'Provider for this preview' not in p.stdout, p.stdout[-500:]
    assert 'ABORTED [PIN' in p.stdout, p.stdout[-500:]


def test_local_preview_flag_only_in_ollama_mode():
    """DELIVERY_LOCAL_PREVIEW=1 offers the ollama route; without OLLAMA_BASE_URL
    (deepseek mode) that route is dead and fails task starts with a connection
    error. The flag must be written exactly once: by lib.sh's preview_ollama_env,
    which the launcher calls only inside the ollama branch."""
    src = SCRIPT.read_text()
    lib = LIB.read_text()
    assert lib.count('DELIVERY_LOCAL_PREVIEW=1') == 1, 'the flag is written exactly once, in preview_ollama_env'
    fn = lib.split('preview_ollama_env(){', 1)[1].split('\n}', 1)[0]
    assert 'DELIVERY_LOCAL_PREVIEW=1' in fn
    assert 'DELIVERY_LOCAL_PREVIEW=1' not in src, 'the launcher never writes the flag itself'
    block = 'if [ "$PROVIDER" = ollama ]; then\n  preview_ollama_env "$WORK/preview.env" "$OLLAMA_MODEL" "$OLLAMA_HOST"'
    assert block in src, 'the flag must be written inside the ollama provider branch'


def test_key_never_written_or_printed(tmp_path):
    """Every abort path: the key value appears nowhere in output, and no file
    under the temp/settings locations contains it."""
    for env_extra in ({}, {'DEEPSEEK_API_KEY': KEY}):
        p = _run(tmp_path, env_extra, settings=_settings(tmp_path))
        assert KEY not in p.stdout and KEY not in p.stderr
    for f in tmp_path.rglob('*'):
        if f.is_file() and f.stat().st_size < 1024 * 1024:
            assert KEY not in f.read_text(errors='replace'), f

def test_preview_sh_pins_the_expected_build():
    """preview.sh is no longer frozen: 2026-10-27 it gained the REQUI_EXPECTED_REPOS pin
    (both preview paths) so the container's staleness check can compare the running image
    against the build the launcher expected. Keep both pins, after the build_env calls."""
    src = (REPO / 'scripts' / 'ops' / 'preview.sh').read_text()
    assert src.count("printf 'REQUI_EXPECTED_REPOS=%s\\n' \"$REPOS\" >> \"$WORK/preview.env\"") == 2

def test_launcher_structure_guards():
    """Static invariants: key passed via -e only, never appended to an env file;
    cleanup removes the container and the temp folder on every exit path."""
    src = SCRIPT.read_text()
    assert 'trap cleanup EXIT INT TERM' in src
    assert 'rm -rf "$WORK"' in src and 'docker rm -f "$NAME"' in src
    assert '-e DEEPSEEK_API_KEY' in src
    build = src[src.index('build_exec_env(){'):src.index('for t in docker curl git shasum tar')]
    assert 'DEEPSEEK_API_KEY=' not in build, 'the key value must never be written into the temp env file'
    assert 'grep -v -i -E' in build, 'the provider-key strip filter from the preview settings builder must be kept'
    assert 'DELIVERY_CLOUD_EXECUTION=1' in build
    assert "printenv DEEPSEEK_API_KEY" not in src and 'echo "$DEEPSEEK_API_KEY"' not in src


def _real_git_layout(tmp_path, kind):
    """A scripts/ops copy in a real git layout: primary checkout, linked
    worktree, or copied-out (no repo). Uses the real git binary so the
    detection semantics under test are git's own. No origin is configured,
    so a detected checkout stops at the REMOTE gate before any network."""
    base = tmp_path / kind
    if kind == 'primary':
        base.mkdir()
        subprocess.run(['git', 'init', '-q', str(base)], check=True)
    elif kind == 'linked':
        primary = tmp_path / 'linked-src'
        subprocess.run(['git', 'init', '-q', str(primary)], check=True)
        subprocess.run(['git', '-C', str(primary), '-c', 'user.email=t@t', '-c', 'user.name=t',
                        'commit', '-q', '--allow-empty', '-m', 'x'], check=True)
        subprocess.run(['git', '-C', str(primary), 'worktree', 'add', '-q', '--detach', str(base)],
                       check=True, capture_output=True)
    else:  # copied-out: no git init at all
        base.mkdir()
    ops = base / 'scripts' / 'ops'
    ops.mkdir(parents=True, exist_ok=True)
    for f in ('preview-exec.sh', 'lib.sh', 'pin.py'):
        (ops / f).write_text((REPO / 'scripts' / 'ops' / f).read_text())
    return base


def _run_real_git(script_base):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(script_base)}
    return subprocess.run(['bash', str(script_base / 'scripts' / 'ops' / 'preview-exec.sh')],
                          input='no\n', capture_output=True, text=True, cwd=str(script_base),
                          env=env, timeout=30)


def test_repo_detection_accepts_primary_checkout(tmp_path):
    p = _run_real_git(_real_git_layout(tmp_path, 'primary'))
    assert 'ABORTED [NO-REPO]' not in p.stdout
    assert 'ABORTED [REMOTE]' in p.stdout, p.stdout[-300:]


def test_repo_detection_accepts_linked_worktree(tmp_path):
    """A linked worktree has a .git file, not a directory - the old
    [ -d .git ] check aborted NO-REPO on a perfectly good checkout."""
    p = _run_real_git(_real_git_layout(tmp_path, 'linked'))
    assert 'ABORTED [NO-REPO]' not in p.stdout
    assert 'ABORTED [REMOTE]' in p.stdout, p.stdout[-300:]


def test_repo_detection_rejects_copied_out_script(tmp_path):
    p = _run_real_git(_real_git_layout(tmp_path, 'copied'))
    assert 'ABORTED [NO-REPO]' in p.stdout, p.stdout[-300:]

def test_ollama_pin_abort_points_at_the_pin_fail_fix_not_a_vendor_page(tmp_path):
    """Regression: in ollama mode a PIN-FAIL is a local server/model/context
    condition whose line names its own fix (start the server, pull the model,
    raise OLLAMA_CONTEXT_LENGTH). The abort advice must point at that line.
    The 'page or the parser changed' text belongs to the deepseek pin, which
    scrapes vendor pages - it sent an operator hunting for a parser bug when
    the Ollama server simply ran with the default 4096 context."""
    tty = tmp_path / 'tty'
    tty.write_text('yes\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama',
                        'OLLAMA_HOST': 'http://127.0.0.1:9'},
             settings=_settings(tmp_path))
    assert 'ABORTED [PIN]' in p.stdout, p.stdout[-500:]
    assert 'PIN-FAIL' in p.stdout
    assert 'page or the parser changed' not in p.stdout
    assert 'the PIN-FAIL line above names' in p.stdout


def test_phi4_is_the_default_local_model(tmp_path):
    """Shy's local-preview model is phi4 ("a quicker engine, rather than an
    accurate one", 2026-10-25); qwen2.5-coder:14b here was an unnoticed drift
    (delivery's own DEFAULT_OLLAMA_MODEL is phi4). Engine stage defaults stay
    qwen - this is the preview override only, in both the launcher and the pin."""
    tty = tmp_path / 'tty'
    tty.write_text('no\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama'},
             settings=_settings(tmp_path))
    assert 'Provider: local Ollama model phi4' in p.stdout, p.stdout[-800:]
    pin = (REPO / 'scripts' / 'ops' / 'pin-local.py').read_text()
    assert "'phi4'" in pin and 'qwen2.5-coder:14b' not in pin


def test_the_plan_is_printed_up_front_with_expected_durations(tmp_path):
    """Fail-fast redesign: the whole step list with expected durations prints
    BEFORE step 1 runs, so a slow step is never a mystery wait."""
    tty = tmp_path / 'tty'
    tty.write_text('no\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama'},
             settings=_settings(tmp_path))
    plan = p.stdout.index('Plan (')
    step1 = p.stdout.index('== [1/')
    assert plan < step1
    for word in ('source smoke', 'build', 'image smoke', 'start'):
        assert word in p.stdout[:step1], word


def test_a_failure_names_the_step_and_how_long_it_ran(tmp_path):
    tty = tmp_path / 'tty'
    tty.write_text('no\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama'},
             settings=tmp_path / 'absent.env')   # unprovisioned: fails inside step 1
    assert '== [1/' in p.stdout
    assert 'ABORTED [NOT-PROVISIONED]' in p.stdout
    assert 'Step 1/' in p.stdout and 'failed after' in p.stdout, p.stdout[-400:]


def test_preview_env_pins_the_expected_build():
    """The container's build-staleness check needs REQUI_EXPECTED_REPOS in the
    preview env. preview-exec builds its env itself (build_exec_env), so the
    #263 preview.sh pin never covered this path - it kept skipping."""
    src = SCRIPT.read_text()
    build = src[src.index('build_exec_env(){'):]
    build = build[:build.index('\n}')]
    assert 'REQUI_EXPECTED_REPOS' in build


def test_the_build_is_not_asked_a_second_yes():
    """Approved drop: invoked from preview-exec the build asks nothing of its
    own (REQUI_BUILD_YES=1); the preview's one typed yes covers it. Standalone
    runs keep their own yes (tests/test_ops_build_local_image.py)."""
    src = SCRIPT.read_text()
    assert 'REQUI_BUILD_YES=1' in src
    build = (REPO / 'scripts' / 'docker' / 'build-local-image.sh').read_text()
    assert 'REQUI_BUILD_YES' in build


def test_the_grounding_step_says_it_loads_the_model(tmp_path):
    """The ollama grounding step LOADS the model on this Mac (minutes of fan
    noise) so the server reports the context window it will actually use -
    the step must say that up front, not look like a hang."""
    tty = tmp_path / 'tty'
    tty.write_text('yes\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama',
                        'OLLAMA_HOST': 'http://127.0.0.1:9'}, settings=_settings(tmp_path))
    assert 'loads the model' in p.stdout, p.stdout[-800:]


FULL_DOCKER = r'''#!/usr/bin/env bash
case "$1" in
  info) exit 0;;
  ps) case "$*" in *-a*) exit 0;; *) echo requi-preview-exec;; esac;;
  image)
    case "$*" in
      *Architecture*) case $(/usr/bin/uname -m) in arm64|aarch64) echo arm64;; *) echo amd64;; esac;;
      *) exit 0;;
    esac;;
  run)
    case "$*" in
      *full-smoke.py*) echo "smoke ok";;
      *psycopg*) echo DBOK;;
      *full_entry*) echo CONTRACT-OK;;
      *) echo container-id;;
    esac;;
  logs) echo "boot"; echo "Cloud execution: OPEN";;
  exec) printf 'EXEC=1\nKEYS=0\n';;
  rm|tag|rmi) :;;
esac
exit 0
'''

def _ollama_server():
    import http.server, threading, json

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/version':
                self._send({'version': '0.0.0-test'})
            elif self.path == '/api/ps':
                self._send({'models': [{'model': 'phi4:latest', 'context_length': 32768}]})
            else:
                self._send({})

        def do_POST(self):
            self.rfile.read(int(self.headers.get('content-length') or 0))
            self._send({})

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_full_run_reaches_ready_with_the_steps_in_order(tmp_path):
    """The whole launcher end to end, hermetically: a fake Ollama server, a
    docker stub playing a passing reuse-smoke and start, real bash, the real
    pin-local grounding. The real code path, not a mocked seam - both preflight
    regressions this week shipped through seams their tests mocked."""
    server = _ollama_server()
    bin_dir = _stubbin(tmp_path)
    (bin_dir / 'docker').write_text(FULL_DOCKER)
    (bin_dir / 'docker').chmod(0o755)
    tty = tmp_path / 'tty'
    tty.write_text('yes\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'ollama',
                        'OLLAMA_HOST': 'http://127.0.0.1:%d' % server.server_address[1]},
             settings=_settings(tmp_path))
    assert p.returncode == 0, p.stdout[-1500:]
    assert 'PREVIEW-EXEC READY' in p.stdout, p.stdout[-1500:]
    markers = ['== [%d/' % k for k in range(1, 9)]
    positions = [p.stdout.index(m) for m in markers]
    assert positions == sorted(positions), 'the 8 steps must run in the printed order'
    assert 'Provider: local Ollama model phi4' in p.stdout
    assert 'Type or paste' not in p.stdout and 'ABORTED' not in p.stdout
    assert 'MODEL      phi4' in p.stdout   # the real pin-local grounding ran against the fake server


def test_the_venv_gate_passes_with_the_real_venv_and_fails_loud_without_site_packages():
    """The step-1 venv gate, exercised for real both ways: the checkout's own
    .venv carries the declared deps (psycopg_pool included since the pool
    redesign); a bare interpreter without site-packages fails and names the
    exact per-module pip line (2026-10-27: his venv predated psycopg-pool and
    the launcher died inside a pytest traceback in step 2 instead)."""
    import subprocess
    script = REPO / 'scripts' / 'ops' / 'venv_check.py'
    venv_py = REPO / '.venv' / 'bin' / 'python'
    ok = subprocess.run([str(venv_py), str(script)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    bare = subprocess.run([str(venv_py), '-S', str(script)], capture_output=True, text=True)
    assert bare.returncode == 1
    assert 'missing in .venv' in bare.stdout, bare.stdout
    assert ".venv/bin/python -m pip install" in bare.stdout and 'psycopg-pool' in bare.stdout


def test_the_venv_gate_runs_in_step_1_before_any_pytest():
    """A missing declared dep must abort in step 1 with the fix command, not
    inside a step-2 pytest import traceback."""
    src = SCRIPT.read_text()
    step1 = src.index('step "inputs and environment"')
    gate = src.index('venv_check.py')
    pytest_step = src.index('-m pytest')
    assert step1 < gate < pytest_step
