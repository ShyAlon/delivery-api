"""The preview's browser gate repairs the one environment failure it can:
exit 2 (no Chromium for this interpreter) triggers one install + one retry."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'docker'))
from local_image_preview import run_browser_smoke


def _run(results, calls):
    def fake(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=results.pop(0), stdout='out', stderr='')
    return fake


def test_a_missing_browser_installs_once_and_retries_the_gate():
    calls = []
    result = run_browser_smoke('/venv/python', '/repo/browser_smoke.py', 'http://127.0.0.1:8001',
                               run=_run([2, 0, 0], calls))
    assert result.returncode == 0
    assert calls[1] == ['/venv/python', '-m', 'playwright', 'install', 'chromium']
    assert len(calls) == 3, 'smoke, install, one retried smoke - never more'


def test_a_real_boot_failure_never_triggers_an_install():
    calls = []
    result = run_browser_smoke('/venv/python', '/repo/browser_smoke.py', 'http://127.0.0.1:8001',
                               run=_run([1], calls))
    assert result.returncode == 1 and len(calls) == 1


def test_a_failed_install_is_named_in_the_returned_output():
    calls = []
    def failing(args, **kw):
        calls.append(args)
        if 'install' in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout='', stderr='network unreachable')
        return subprocess.CompletedProcess(args=args, returncode=2, stdout='no browser', stderr='')
    result = run_browser_smoke('/venv/python', '/repo/browser_smoke.py', 'http://127.0.0.1:8001', run=failing)
    assert result.returncode == 2 and 'network unreachable' in result.stdout
