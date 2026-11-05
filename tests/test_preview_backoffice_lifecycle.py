"""Private listener lifetime moved from extracted shell to an owned Python API.

Real bind/health/private-log/collision/owned-close tests live in
 test_image_private_backoffice.py; preview calls it only after image/browser
readiness. No shell-function body is copied into a parallel implementation.
"""
from pathlib import Path


def test_preview_owns_private_backoffice_after_browser_readiness():
    text=(Path(__file__).resolve().parents[1]/'scripts/docker/local_image_preview.py').read_text()
    assert text.index('if result.returncode:')<text.index('backoffice.start()')
    assert 'finally:backoffice.close()' in text
    assert 'Ctrl-C closes this listener, preview container is retained' in text
