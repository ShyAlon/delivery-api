"""An installed runtime never stores executable/folder choices beside its code."""
from fastapi.testclient import TestClient
import pytest
from delivery_api import app as module

@pytest.mark.parametrize('path',['/api/settings/artifacts-root','/api/settings/engine-python'])
def test_host_settings_routes_are_absent(path,pg_scoped_workspace,pg_task_app):
    client=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers)
    assert path not in {route.path for route in module.app.routes}
    assert client.get(path).status_code==404
    assert client.post(path,json={'path':'/tmp/arbitrary'}).status_code==404
