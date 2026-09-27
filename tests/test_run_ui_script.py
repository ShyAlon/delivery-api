from pathlib import Path

def test_run_ui_script_is_self_contained_and_prints_url():
    p=Path(__file__).parents[1]/"scripts/run-ui.sh";s=p.read_text()
    assert p.stat().st_mode & 0o111
    assert 'http://$HOST:$PORT' in s
    assert '-m pip install -e "$ROOT"' in s
    assert 'from vmodel_contracts import CANONICAL_STAGE_IDS' in s
    assert 'scope_tiebreak' in s
    assert 'syncing delivery-api and UI dependencies' in s
    assert "Error:" in s and "Likely cause:" in s and "Next:" in s

def test_run_ui_script_starts_the_interview_engine_child():
    # Development parity with the packaged launcher: the interview flow needs
    # the engine HTTP service and nothing else starts it in development.
    s=(Path(__file__).parents[1]/"scripts/run-ui.sh").read_text()
    assert 'interview_engine.http_service:app' in s
    assert 'INTERVIEW_ENGINE_PORT' in s
    assert 'INTERVIEW_ENGINE_URL' in s and 'INTERVIEW_ENGINE_TOKEN' in s
    assert '"$PYTHON" -m delivery_api &' in s
    assert 'trap shutdown EXIT INT TERM' in s
    assert 'import interview_engine,uvicorn,langchain_core' in s
    # An orphaned pre-pull engine is replaced, never silently reused.
    assert 'Replacing orphaned interview engine' in s
