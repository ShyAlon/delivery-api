"""Explicit PG-backed local compatibility in a separate import profile."""
import inspect
import pytest


@pytest.fixture
def pg_local_compat_process(pg_runtime_process):
    def run(check):
        code = '''
import tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
sys.path.insert(0,str(Path.cwd()/'tests'))
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from delivery_api import config
# Select the tested local profile before app import, not by removing middleware.
# DATABASE_URL remains disposable PG and the runtime fixture poisons SQLite.
config.CLOUD_TENANTS=False
from delivery_api.app import app,store
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope,tenant_context
assert isinstance(store,PostgresStore)
''' + inspect.getsource(check) + '''
with tempfile.TemporaryDirectory() as scratch,pytest.MonkeyPatch.context() as monkeypatch:
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
    with tenant_context(scope):
        CHECK(monkeypatch,Path(scratch),SimpleNamespace(store=store,scope=scope))
'''.replace('CHECK(',check.__name__+'(')
        result=pg_runtime_process(code)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
    return run
