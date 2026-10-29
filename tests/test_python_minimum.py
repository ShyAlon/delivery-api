"""Declared runtime floor must exclude Python without hashlib.file_digest."""
from pathlib import Path
import re
from packaging.specifiers import SpecifierSet


def test_declared_python_minimum_matches_lock_and_digest_requirement():
    root = Path(__file__).parents[1]
    def requirement(name):
        return re.search(r'^requires-python = "([^"]+)"', (root / name).read_text(), re.M).group(1)
    declared = requirement('pyproject.toml')
    assert declared == requirement('uv.lock') == '>=3.11'
    allowed = SpecifierSet(declared)
    assert '3.10.99' not in allowed
    assert '3.11.0' in allowed and '3.12.0' in allowed
