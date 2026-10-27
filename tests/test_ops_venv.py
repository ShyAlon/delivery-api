"""ops_venv (scripts/ops/lib.sh): the ops venv must carry the declared extras.

2026-10-27: the pool redesign added psycopg-pool (pyproject supabase/dev
extras) but ops_venv installed '-e .' without extras - existing venvs never
received it and a fresh one would not either. He approved installing
'.[dev,supabase]'. Hermetic: a stub .venv/bin/python decides the import probe
and records the pip call; real bash runs the real lib.sh function.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'scripts' / 'ops' / 'lib.sh'

STUB_PY = '''#!/usr/bin/env bash
if [ "$1" = "-c" ]; then exit "$PROBE_RC"; fi   # the import probe
if [ "$1" = "-m" ]; then echo "$@" >> "$FAKE_LOG"; exit 0; fi
exit 0
'''


def _venv(tmp_path, probe_rc):
    d = tmp_path / '.venv' / 'bin'
    d.mkdir(parents=True)
    py = d / 'python'
    py.write_text(STUB_PY)
    py.chmod(0o755)
    log = tmp_path / 'log'
    log.write_text('')
    return log, {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'FAKE_LOG': str(log), 'PROBE_RC': str(probe_rc)}


def _run_ops_venv(tmp_path, env):
    return subprocess.run(['bash', '-c', '. "%s"; ops_venv' % LIB], cwd=tmp_path,
                          capture_output=True, text=True, env=env, timeout=30)


def test_an_existing_venv_missing_the_extras_is_refreshed(tmp_path):
    """An existing venv that fails the psycopg_pool probe gets the declared
    extras installed - no manual line, no venv rebuild."""
    log, env = _venv(tmp_path, probe_rc=1)
    p = _run_ops_venv(tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    calls = log.read_text()
    assert '-m pip install' in calls and '.[dev,supabase]' in calls, calls
    assert 'refresh' in p.stdout.lower()


def test_a_current_venv_is_left_alone(tmp_path):
    """Probe passes: no pip call, no output noise, straight through."""
    log, env = _venv(tmp_path, probe_rc=0)
    p = _run_ops_venv(tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert log.read_text() == ''


def test_a_failed_refresh_aborts_without_deleting_the_venv(tmp_path):
    """A network blip during the refresh must not destroy a venv that still
    works for everything else: abort, name the failure, leave .venv in place."""
    log, env = _venv(tmp_path, probe_rc=1)
    py = tmp_path / '.venv' / 'bin' / 'python'
    py.write_text(STUB_PY.replace('exit 0; fi\nexit 0', 'exit 1; fi\nexit 0'))   # pip fails too
    p = _run_ops_venv(tmp_path, env)
    assert p.returncode == 1
    assert 'ABORTED [VENV-INSTALL]' in p.stdout, p.stdout[-400:]
    assert (tmp_path / '.venv' / 'bin' / 'python').exists(), 'the venv must be left in place'


def test_a_fresh_venv_installs_the_declared_extras():
    """The create path (source assertion - a real venv build is too heavy for
    the suite): the install line carries the extras, so a fresh venv has
    psycopg-pool from birth."""
    src = LIB.read_text()
    create = src.split('ops_venv(){', 1)[1]
    assert "-e '.[dev,supabase]'" in create
