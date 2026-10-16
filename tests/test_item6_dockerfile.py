import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / 'scripts' / 'item6' / 'Dockerfile'


def _instructions():
    text = DOCKERFILE.read_text().replace('\\\n', ' ')
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]


def test_dockerfile_builds_on_the_legacy_builder():
    # The server has no buildx, so BuildKit-only syntax fails the build there.
    raw = DOCKERFILE.read_text()
    assert not re.search(r'^\s*#\s*syntax\s*=', raw, re.M)
    for line in _instructions():
        word = line.split(None, 1)[0].upper()
        if word in {'COPY', 'ADD'}:
            assert not re.search(r'--(chmod|link|parents|exclude)\b', line), line
        if word == 'RUN':
            assert not re.search(r'--(mount|network|security)\b', line), line
        assert '<<' not in line, line


def test_item6_files_are_made_readable_for_the_container_user():
    runs = [line for line in _instructions() if line.upper().startswith('RUN ')]
    assert any('chmod -R a+rX' in line and '/app/item6' in line and '/app/migrations' in line for line in runs)
