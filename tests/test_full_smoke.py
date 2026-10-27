"""full-smoke.py must say WHERE it died and WHY, every time.

2026-10-27: an image smoke failed and the operator saw only "smoke FAILED
(output above)" with nothing above. Two holes: full-smoke printed no stage
markers, and a failure outside the early-exit branch lost the entry log tail.
Here the engine package is deliberately absent, so the entry's preflight
refuses and full-smoke must name the stage, the reason, and the entry's own
report - a real subprocess run, no docker needed."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _has_engine():
    try:
        import interview_engine  # noqa
        return True
    except ImportError:
        return False


def test_a_refused_entry_is_reported_with_stage_reason_and_entry_report():
    if _has_engine():
        import pytest
        pytest.skip('needs an environment without interview_engine installed')
    out = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'docker' / 'full-smoke.py')],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode != 0
    text = out.stdout + out.stderr
    assert 'smoke stage:' in text, text
    assert 'SMOKE FAILED' in text, text
    assert 'engine-compatible' in text, text   # the entry's own preflight reason survives
