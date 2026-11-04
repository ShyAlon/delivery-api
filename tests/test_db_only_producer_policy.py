import ast,inspect
from delivery_api.same_host_producer import SameHostProducerWorker

def test_producer_has_no_file_storage_selector():
    source=inspect.getsource(SameHostProducerWorker)
    assert 'ARTIFACT_PUBLISH' not in source
    assert 'documents_enabled' not in source
    assert 'publish_session_revision' not in source
    assert 'StopFence(' not in source
    assert '_produced_set_hash' not in source
