from types import SimpleNamespace
from delivery_api import task_read_cache as cache

class Store:pass

def test_invalidate_never_walks_entries_and_stale_data_remains_unreadable(monkeypatch):
    from delivery_api import config
    monkeypatch.setattr(config,'CLOUD_TENANTS',False);cache.clear();s=Store();other=Store()
    cache.put(s,'one',{'v':1},read_version=cache.version(s,'one'))
    cache.put(other,'two',{'v':2},read_version=cache.version(other,'two'))
    class NoWalk(dict):
        def __iter__(self):raise AssertionError('write scanned cache')
    monkeypatch.setattr(cache,'_entries',NoWalk(cache._entries))
    cache.invalidate(s)
    assert cache.get(s,'one') is None
    assert cache.get(other,'two')=={'v':2}
    cache.put(s,'one',{'v':3},read_version=cache.version(s,'one'))
    assert cache.get(s,'one')=={'v':3}
