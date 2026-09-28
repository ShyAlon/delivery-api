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


def test_local_launcher_reports_missing_generator_before_starting_server(tmp_path):
    import os, subprocess
    source=Path(__file__).parents[1]
    # A wrapper satisfies only the launcher's earlier delivery import check.
    # No project install and no server process occur before the preflight.
    fake=tmp_path/'python';fake.write_text('#!/bin/sh\nexit 0\n');fake.chmod(0o755)
    env={**os.environ,'SPEC_GENERATOR_DIR':str(tmp_path/'missing-generator'),
         'PYTHON':str(fake),'XDG_DATA_HOME':str(tmp_path/'data')}
    result=subprocess.run(['bash',str(source/'scripts/run-ui.sh')],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==1,result.stderr
    assert 'spec-generator environment is missing' in result.stdout
    assert 'Next: run scripts/pull-all.sh' in result.stdout
