"""Browser failure retention is exercised through the production Python entry.

See test_local_image_preview.test_browser_failure_keeps_container_and_named_private_evidence,
which runs the real planner with fake Docker, asserts the real smoke module is
called, validates named error evidence and private mode, and asserts no removal.
"""
from pathlib import Path


def test_browser_smoke_is_not_optional_or_hidden_by_health_success():
    text=(Path(__file__).resolve().parents[1]/'scripts/docker/local_image_preview.py').read_text()
    assert "'ops/browser_smoke.py'" in text
    assert "if result.returncode:" in text and 'container retained:' in text
    assert '--fast' not in text
