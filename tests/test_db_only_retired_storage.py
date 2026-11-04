import importlib.util,inspect
from pathlib import Path
from delivery_api import artifact_publication

def test_restore_backfill_and_file_publication_are_retired():
    for name in ('artifact_restore','artifact_backfill','revision_install_files'):
        assert importlib.util.find_spec('delivery_api.'+name) is None
    for name in ('publish_session_revision','publish_committed_session','session_artifact_files','pin_settled_artifacts','file_artifact_snapshot'):
        assert not hasattr(artifact_publication,name)
    assert not (Path(__file__).parents[1]/'scripts/ops-backfill-artifact-manifest.py').exists()

def test_store_has_no_filesystem_publication_adapter():
    from delivery_api.postgres_store import PostgresStore
    assert not hasattr(PostgresStore,'publish_task_artifact')
    assert not hasattr(PostgresStore,'publish_task_revision')
