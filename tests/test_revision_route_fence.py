"""Acceptance/install helpers and private assets remain unreachable from HTTP."""
import ast
from pathlib import Path
from fastapi.testclient import TestClient
from delivery_api import app as service,config


def test_private_acceptance_and_install_not_exposed_as_routes_or_assets(monkeypatch):
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    client=TestClient(service.app)
    for path in ('/revision-acceptance.html','/revision-acceptance.js','/private-ui/revision-acceptance.html',
                 '/api/tasks/fixture/revision-install','/api/tasks/fixture/revision-acceptance'):
        assert client.get(path).status_code==404
        assert client.post(path,json={'accepted':True,'review_sha256':'a'*64}).status_code==404
    routes=[getattr(route,'path','') for route in service.app.routes]
    assert not any('revision-install' in route or 'revision-acceptance' in route for route in routes)
    tree=ast.parse(Path(service.__file__).read_text())
    forbidden={'revision_acceptance_view','revision_install_coordinator','revision_install_files'}
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):assert not any(name in (node.module or '') for name in forbidden)
        if isinstance(node,ast.Import):assert not any(any(name in alias.name for name in forbidden) for alias in node.names)
