import inspect
from delivery_api import bundles

def test_bundle_module_has_no_host_discovery_or_settings():
    for name in ('root','save_root','discover','bundle','_discover','inspect_root'):
        assert not hasattr(bundles,name)
    source=inspect.getsource(bundles)
    assert 'read_text' not in source and 'write_text' not in source
