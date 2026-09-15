from pathlib import Path

def test_run_ui_script_is_self_contained_and_prints_url():
    p=Path(__file__).parents[1]/"scripts/run-ui.sh";s=p.read_text()
    assert p.stat().st_mode & 0o111
    assert 'http://$HOST:$PORT' in s
    assert '-m pip install -e "$ROOT"' in s
    assert 'from vmodel_contracts import CANONICAL_STAGE_IDS' in s
    assert 'scope_tiebreak' in s
    assert 'syncing delivery-api and UI dependencies' in s
    assert 'exec "$PYTHON" -m delivery_api' in s
    assert "Error:" in s and "Likely cause:" in s and "Next:" in s
