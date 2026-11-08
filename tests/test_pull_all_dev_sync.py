"""pull-all.sh must sync the dev extra so owner steps never abort mid-ritual.

Fail-first on main: pull-all.sh installs runtime dependencies only and
venv_check.py does not cover playwright, so both tests are red.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pull_all_installs_the_declared_dev_extra():
    text = (ROOT / 'scripts/pull-all.sh').read_text()
    assert 'optional-dependencies' in text and 'dev_extra=' in text, 'the sync must detect a declared dev extra'
    assert '${dev_extra:+--extra dev}' in text, 'uv sync must install the dev extra when declared'
    assert '${dev_extra:+[dev]}' in text, 'the pip fallback must install the dev extra when declared'


def test_venv_gate_covers_the_browser_smoke_dependency():
    text = (ROOT / 'scripts/ops/venv_check.py').read_text()
    assert "'playwright': 'playwright>=1.48'" in text, 'the preview smoke imports playwright from this venv'
    assert "'pytest': 'pytest>=8'" in text
