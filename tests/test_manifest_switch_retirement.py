"""PG publication is unconditional; the retired read-switch tool is absent."""
from pathlib import Path


def test_no_legacy_manifest_switch_or_coverage_entrypoint():
    from delivery_api import app
    assert not hasattr(app,'artifact_manifest_coverage')
    source=Path(app.__file__).read_text()
    assert 'config.ARTIFACT_MANIFEST_READS' not in source
