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
    p = _run(tmp_path, {}, settings=_settings(tmp_path))
    assert 'ABORTED [NO-KEY]' in p.stdout, p.stdout[-400:]
    assert p.returncode == 1

def test_short_or_spaced_key_aborts(tmp_path):
    for bad in ('sk-short', 'sk-has space init'):
        p = _run(tmp_path, {'DEEPSEEK_API_KEY': bad}, settings=_settings(tmp_path))
        assert 'ABORTED [KEY-' in p.stdout, (bad, p.stdout[-300:])

def test_unprovisioned_settings_abort(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY}, settings=tmp_path / 'absent.env')
    assert 'ABORTED [NOT-PROVISIONED]' in p.stdout

def test_settings_mode_must_be_600(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY}, settings=_settings(tmp_path, mode=0o644))
    assert 'ABORTED [SETTINGS-PERMS]' in p.stdout

def test_settings_without_database_aborts(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY}, settings=_settings(tmp_path, body='DELIVERY_SUPABASE_URL=https://x\n'))
    assert 'ABORTED [SETTINGS]' in p.stdout

def test_declined_confirmation_changes_nothing(tmp_path):
    p = _run(tmp_path, {'DEEPSEEK_API_KEY': KEY}, settings=_settings(tmp_path), tty_input='no\n')
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout

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
