"""Every test file must parse under the repo's Python. A file that cannot be
collected is a suite that silently never runs: 2026-10-27,
tests/test_progress_seven_phase_parity.py used a PEP 701 f-string (py3.12+)
and pytest reported one collection error while its parity coverage sat dead."""
import ast
from pathlib import Path

import pytest

TEST_FILES = sorted((Path(__file__).resolve().parent).glob('test_*.py'))


@pytest.mark.parametrize('path', TEST_FILES, ids=[p.name for p in TEST_FILES])
def test_the_file_parses_under_this_python(path):
    ast.parse(path.read_text(errors='replace'), filename=str(path))
