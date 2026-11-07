"""The prose-free control-path check trips on banned branches and passes on main.

Fail-first companions: on main the gate job does not run the check (see
tests/test_ci_fast_gate.py) and the old string-marker script cannot answer
these questions at all.
"""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_process_control', ROOT / 'scripts/ci/check-process-control.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PROSE_FIXTURE = '''
from fastapi import FastAPI
app = FastAPI()

@app.post("/api/tasks/{task_id}/start")
async def start_task_engine(task_id: str):
    if reply == "Approve this intent model?":
        return {"status": "accepted"}
    return {"status": "ok"}

@app.post("/api/tasks/{task_id}/answer")
async def answer_interview(task_id: str):
    return {"status": "ok"}

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    return {}
'''

CLEAN_FIXTURE = PROSE_FIXTURE.replace('if reply == "Approve this intent model?":', 'if reply == "accepted":')


def write(tmp_path, text):
    f = tmp_path / 'app.py'
    f.write_text(text)
    return f


def test_prose_branch_in_a_control_route_trips(tmp_path):
    errors = module.check(app_path=write(tmp_path, PROSE_FIXTURE), whole=())
    assert any('prose controls process' in e for e in errors), errors


def test_missing_control_route_is_loud(tmp_path):
    errors = module.check(app_path=write(tmp_path, 'from fastapi import FastAPI\napp = FastAPI()\n'), whole=())
    assert any('missing or re-routed' in e for e in errors), errors


def test_clean_fixture_passes(tmp_path):
    assert module.check(app_path=write(tmp_path, CLEAN_FIXTURE), whole=()) == []


def test_current_control_path_is_clean():
    assert module.check() == []
