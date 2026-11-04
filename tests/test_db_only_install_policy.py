import inspect
from delivery_api import revision_install_coordinator as coordinator

def test_installation_coordinator_has_no_host_destination():
    source=inspect.getsource(coordinator)
    for legacy in ('hasattr(', 'session_root(', 'prepare_execution_anchor', 'promote_private_stage', 'os.open(', 'fcntl'):
        assert legacy not in source
    assert 'destination_guard' in source and 'read_evidence' in source
