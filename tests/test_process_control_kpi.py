from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_http_interview_path_has_no_subprocess_or_stdin():
 text=(ROOT / 'src/delivery_api/app.py').read_text();start=text.index('@app.post("/api/tasks/{task_id}/start")');end=text.index('class RequirementResolution',start);block=text[start:end]
 assert 'runner.run' not in block and 'stdin' not in block and 'interview_engine.cli' not in block

def test_production_engine_projection_has_no_prompt_comparisons():
 text=(ROOT / 'src/delivery_api/engine_projection.py').read_text()
 assert '.prompt' not in text and 're.' not in text
