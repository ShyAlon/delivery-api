"""Migrated browser suites must use the authenticated PG fixture, no local boot."""
from pathlib import Path
import pytest

@pytest.mark.parametrize('filename',['test_decision_pages_browser.py','test_validation_panel_browser.py'])
def test_typed_browser_suite_has_no_local_database_bootstrap(filename):
    source=(Path(__file__).parent/filename).read_text()
    assert 'pg_browser' in source
    assert 'subprocess.Popen' not in source
    assert 'DELIVERY_DB_PATH' not in source
