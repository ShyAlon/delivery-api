"""The no-migration policy must not ship an alternate historical-data importer."""
from pathlib import Path
import importlib.util
import re

ROOT=Path(__file__).parents[1]

def test_retired_importers_are_not_installed_or_available():
    for name in ('legacy_import','legacy_ownership'):
        assert importlib.util.find_spec('delivery_api.'+name) is None
        assert not (ROOT/'src/delivery_api'/f'{name}.py').exists()


def test_tenant_ci_only_references_existing_test_files():
    script=(ROOT/'scripts/ci/test-tenant-postgres.sh').read_text()
    tests=re.findall(r'tests/test_[a-z0-9_]+\.py',script)
    assert tests
    assert all((ROOT/path).is_file() for path in tests)
    assert 'tests/test_no_legacy_storage_importers.py' in tests
