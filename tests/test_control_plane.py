import asyncio
import pytest,httpx
from fastapi.testclient import TestClient
from delivery_api import app as service
from delivery_api.engine_client import HttpEngineClient
from delivery_api.runner import ProcessRunner
from delivery_api.jobs import JobQueue


def test_delivery_only_startup_does_not_recover_or_dispatch(monkeypatch):
    monkeypatch.setenv('DELIVERY_CONTROL_PLANE_ONLY','1')
    def fail(*args,**kwargs):raise AssertionError('must not touch engine recovery or queue')
    for name in ('_recover_tasks','_catch_up_interview_progress'):monkeypatch.setattr(service,name,fail)
    monkeypatch.setattr(service.simulation,'reconcile_interrupted',fail)
    monkeypatch.setattr(service.jobs,'start',fail)
    with TestClient(service.app) as client:
        health=client.get('/health').json()
        assert health['runtime_profile']=='control_plane_only' and not health['engine_execution']
        assert client.get('/').status_code==200
        assert client.post('/api/tasks',json={'title':'do not create'}).status_code==503
        assert client.post('/auth/signup',json={}).status_code==503
        assert client.post('/api/payments/webhook',json={}).status_code==503
        assert not service.cloud_execution_admitted()


def test_direct_engine_and_subprocess_and_queue_boundaries_fail_before_effect(monkeypatch,tmp_path):
    monkeypatch.setenv('DELIVERY_CONTROL_PLANE_ONLY','1')
    engine=HttpEngineClient('http://engine.invalid',transport=httpx.MockTransport(lambda _:pytest.fail('network forbidden')))
    with pytest.raises(RuntimeError,match='delivery-only'):engine.default_route()
    runner=ProcessRunner(object())
    with pytest.raises(RuntimeError):asyncio.run(runner.run('task',['python'],cwd=tmp_path))
    with pytest.raises(RuntimeError):asyncio.run(runner._run('task',['python'],cwd=tmp_path))
    queue=JobQueue(object())
    with pytest.raises(RuntimeError):asyncio.run(queue.start())
    with pytest.raises(RuntimeError):asyncio.run(queue.submit('job','run'))
    assert queue._queue.empty() and queue._worker is None


def test_image_has_only_delivery_contracts_nonroot_fixed_launcher():
    from pathlib import Path
    root=Path(__file__).parents[1];docker=(root/'Dockerfile').read_text()
    assert 'COPY . ' not in docker and 'gitconfig' not in docker and 'node:' not in docker
    assert 'USER 10001:10001' in docker and 'delivery_api.control_plane' in docker
    assert 'interview-engine' not in docker and 'spec-generator' not in docker
    ignore=(root/'.dockerignore').read_text()
    assert ignore.startswith('**\n') and '.env' not in ignore


def test_workflow_manual_digest_no_live_secret_gitconfig_or_mutable_tag():
    from pathlib import Path
    root=Path(__file__).parents[1];workflow=(root/'.github/workflows/ci-cd.yml').read_text()
    assert 'needs.image.outputs.digest' in workflow and "vars.CONTROL_PLANE_DEPLOY_ENABLED == 'true'" in workflow
    assert 'type=raw,value=main' not in workflow and 'gitconfig' not in workflow and 'VMODEL_CONTRACTS_PAT' not in workflow
    assert '/etc/requi/control-plane.env' in workflow


def test_private_worker_boundaries_fail_before_import_claim_or_provider(monkeypatch):
    monkeypatch.setenv('DELIVERY_CONTROL_PLANE_ONLY','1')
    from delivery_api.tenant_jobs import execute_job
    from delivery_api.same_host_producer import SameHostProducerWorker
    with pytest.raises(RuntimeError):asyncio.run(execute_job(object(),'job',lambda _:None))
    worker=SameHostProducerWorker(object(),'/tmp/none',gateway_factory=lambda _:None,usage_log_factory=lambda _:None)
    with pytest.raises(RuntimeError):asyncio.run(worker.execute('job',lambda _:None))


def test_image_publish_has_prior_network_none_read_only_container_health_gate():
    from pathlib import Path
    workflow=(Path(__file__).parents[1]/'.github/workflows/ci-cd.yml').read_text()
    assert workflow.index('Dry delivery-only container gate')<workflow.index('docker/login-action')
    assert '--network none --read-only --cap-drop ALL' in workflow
    assert 'docker inspect' in workflow and 'unhealthy' in workflow
