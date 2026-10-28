"""A test-only tripwire, not an executor or compatibility implementation."""
import pytest


@pytest.fixture
def pg_bridge_poison(monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    class PoisonBridge:
        def execute(self,*args,**kwargs):
            raise AssertionError('retired PG query bridge called')
    monkeypatch.setattr(PostgresStore,'_db',PoisonBridge(),raising=False)
    return PoisonBridge
