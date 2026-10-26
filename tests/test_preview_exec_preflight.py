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
KEY = 'sk-testkey-0123456789abcdef'

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

def _run(tmp_path, env_extra, settings=None, tty_input='yes\n'):
    root = tmp_path / 'repos'
    for r in ('delivery-api', 'interview-engine', 'spec-generator', 'verification-loop'):
        (root / r / '.git').mkdir(parents=True, exist_ok=True)
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

def test_deepseek_key_via_hidden_prompt(tmp_path):
    """No key in the environment: the deepseek mode asks on the local terminal
    with echo off (the switch-model.sh pattern), keeps the key in the shell
    only, and continues. The TTY stub's first line feeds the key read; the
    later yes-read then sees the same line and refuses, so reaching
    NOT-CONFIRMED (not NO-KEY) proves the hidden prompt fed a passing key."""
    tty = tmp_path / 'tty'
    tty.write_text(KEY + '\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'deepseek'},
             settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' not in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout, p.stdout[-500:]
    assert KEY not in p.stdout and KEY not in p.stderr, 'the key must never be echoed or printed'

def test_deepseek_hidden_prompt_empty_still_aborts_no_key(tmp_path):
    """An empty answer at the hidden prompt stops with NO-KEY; nothing is
    started and no default or fallback key is invented."""
    tty = tmp_path / 'tty'
    tty.write_text('\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty), 'PREVIEW_EXEC_PROVIDER': 'deepseek'},
             settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-400:]
    assert p.returncode == 1

def test_deepseek_only_mode_carries_no_ollama_settings():
    """DeepSeek-only means no Ollama dependency: the settings handed to the
    container must contain no OLLAMA_* and no DELIVERY_LOCAL_PREVIEW line,
    and the mode must abort OLLAMA-LEAK if one ever appears. Asserted on the
    source: the Ollama writes stay inside the ollama branch, and the guard
    runs in the deepseek branch after the settings file is built."""
    src = SCRIPT.read_text()
    ollama_write = src.split("if [ \"$PROVIDER\" = ollama ]; then\n  printf 'DELIVERY_LOCAL_PREVIEW=1", 1)
    assert len(ollama_write) == 2 and 'OLLAMA_BASE_URL' in ollama_write[1].split('fi', 1)[0], \
        'the Ollama settings writes must stay inside the ollama branch'
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

def test_provider_prompt_pick_deepseek_then_hidden_key(tmp_path):
    """Picking 2 runs the deepseek mode; with no key in the environment the
    next line of the same prompt stream feeds the hidden key read. The key is
    never echoed, and the flow then stops at the typed yes (EOF refuses)."""
    tty = tmp_path / 'tty'
    tty.write_text('2\n' + KEY + '\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty)}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' not in p.stdout, p.stdout[-500:]
    assert 'Provider: DeepSeek only (paid)' in p.stdout, p.stdout[-500:]
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout, p.stdout[-500:]
    assert KEY not in p.stdout and KEY not in p.stderr, 'the key must never be echoed or printed'

def test_provider_prompt_pick_deepseek_empty_key_aborts(tmp_path):
    """Picking 2 and answering the hidden key prompt with an empty line stops
    with NO-KEY; no fallback key or route is invented."""
    tty = tmp_path / 'tty'
    tty.write_text('2\n\n')
    p = _run(tmp_path, {'PREVIEW_TTY': str(tty)}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-400:]
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
    error. The flag must be written exactly once, inside the ollama branch."""
    src = SCRIPT.read_text()
    assert src.count('DELIVERY_LOCAL_PREVIEW=1') == 1
    block = 'if [ "$PROVIDER" = ollama ]; then\n  printf \'DELIVERY_LOCAL_PREVIEW=1\\n\' >> "$WORK/preview.env"'
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

def test_preview_sh_untouched():
    proc = subprocess.run(['git', '-C', str(REPO), 'diff', '--exit-code', 'HEAD', '--', 'scripts/ops/preview.sh'],
                          capture_output=True, text=True)
    assert proc.returncode == 0, 'preview.sh must stay exactly as it is'

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
