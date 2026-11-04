"""Retired control-plane command launches the same authenticated application."""
from fastapi.testclient import TestClient
from delivery_api import app as service


def test_compatibility_command_has_no_runtime_selector():
 from pathlib import Path
 text=(Path(__file__).parents[1]/'src/delivery_api/control_plane.py').read_text()
 assert 'from .__main__ import main' in text
 assert 'os.environ' not in text


def test_unauthenticated_mutation_remains_denied_without_a_global_mode_fence():
 with TestClient(service.app) as client:
  assert client.get('/health').status_code==200
  assert client.get('/').status_code==200
  assert client.post('/api/tasks',json={'title':'must not create'}).status_code==401


def test_workflow_never_deploys_over_ssh_or_stores_private_config():
 from pathlib import Path
 text=(Path(__file__).parents[1]/'.github/workflows/ci-cd.yml').read_text()
 assert 'HETZNER' not in text and 'CONTROL_PLANE_DEPLOY_ENABLED' not in text
 assert 'type=raw,value=main' not in text and 'gitconfig' not in text and 'VMODEL_CONTRACTS_PAT' not in text


def test_one_fixed_nonroot_application_launcher():
 from pathlib import Path
 docker=(Path(__file__).parents[1]/'Dockerfile').read_text()
 assert 'USER 10001:10001' in docker
 assert 'CMD ["python", "-m", "delivery_api"]' in docker
 assert 'DELIVERY_CONTROL_PLANE_ONLY' not in docker
