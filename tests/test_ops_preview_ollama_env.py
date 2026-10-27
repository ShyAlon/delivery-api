"""preview_ollama_env (scripts/ops/lib.sh): the ollama-mode preview settings must
carry the reviewed model for the ENGINE, not only the reviewed price row.

2026-10-27: a preview on the ollama/phi4 route died at its first model call
(scope_tiebreak) with AdmissionDenied 'serialized request bound is unreviewed'.
preview-exec pinned the reviewed $0 row for phi4 but never wrote
DELIVERY_OLLAMA_MODEL into preview.env, so the engine session - started with no
explicit initial model - fell back to its built-in default qwen2.5-coder:14b, a
model the reviewed local gateway has no bound for. The UI showed phi4 the whole
time (model_routes.DEFAULT_OLLAMA_MODEL).

Hermetic: real bash runs the real lib.sh function against a throwaway file.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'scripts' / 'ops' / 'lib.sh'


def _write_env(tmp_path, model='phi4', host='http://localhost:11434'):
    dest = tmp_path / 'preview.env'
    dest.write_text('DELIVERY_DATABASE_URL=sqlite:///x\n')
    proc = subprocess.run(
        ['bash', '-c', '. "%s"; preview_ollama_env "$1" "$2" "$3"' % LIB,
         '_', str(dest), model, host],
        capture_output=True, text=True, env={'PATH': '/usr/bin:/bin'}, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return dest.read_text().splitlines()


def test_the_engine_session_model_travels_with_the_reviewed_row(tmp_path):
    lines = _write_env(tmp_path)
    # The one line whose absence killed the interview start today: without it the
    # engine pins its built-in default, not the reviewed model.
    assert 'DELIVERY_OLLAMA_MODEL=phi4' in lines
    assert 'DELIVERY_LOCAL_PREVIEW=1' in lines
    assert 'OLLAMA_BASE_URL=http://host.docker.internal:11434/v1' in lines


def test_the_model_is_the_callers_not_a_hardcoded_default(tmp_path):
    lines = _write_env(tmp_path, model='phi4:14b', host='http://localhost:22555')
    assert 'DELIVERY_OLLAMA_MODEL=phi4:14b' in lines
    assert 'OLLAMA_BASE_URL=http://host.docker.internal:22555/v1' in lines
