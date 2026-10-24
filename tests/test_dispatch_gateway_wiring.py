"""Process-isolation dispatch supplies the job-bound admission gateway.

Regression coverage for the previously unwired seam: dispatch_phase's process
branch used to reach runner.current_worker_gateway with no gateway installed
anywhere, dying with AdmissionDenied('cloud subprocess admission gateway not
configured'). The dispatch now enters producer_admission.worker_gateway with
the installed factory's job-bound gateway for the run's lifetime.
"""
from types import SimpleNamespace

import pytest

from delivery_api import tenant_jobs
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.producer_admission import current_worker_gateway


def _patch_dispatch(monkeypatch, factory):
    """Run dispatch_phase's process branch with the run machinery stubbed to
    invoke run_child directly against a fake capability."""
    monkeypatch.setenv('DELIVERY_RUN_ISOLATION', 'process')
    from delivery_api import config
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True, raising=False)
    monkeypatch.setattr(config, 'RUN_ISOLATION', 'process', raising=False)
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime, 'gateway_factory', lambda: factory)
    from delivery_api import child_compute
    monkeypatch.setattr(child_compute, 'available', lambda store: False)

    captured = {}

    async def fake_run_limited(store, orchestrator, task_id, work, **kw):
        return await work()

    async def fake_execute_job(store, job_id, handler, **kw):
        capability = SimpleNamespace(task_id='task-1', session_id='sess-1',
                                     validate=lambda store: None)
        await handler(capability)
        return True

    from delivery_api import run_slots as run_slots_module
    monkeypatch.setattr(run_slots_module, 'run_limited', fake_run_limited)
    monkeypatch.setattr(tenant_jobs, 'execute_job', fake_execute_job)
    store = SimpleNamespace(
        enqueue_worker=lambda task_id, kind, execution_options=None: 'job-1',
        worker_job=lambda job_id: {'task_id': 'task-1', 'kind': 'phase:needs', 'state': 'running'},
    )
    return captured, store


def _gateway(**overrides):
    base = dict(job_id='job-1', retail_rates=None, resolve_quote=lambda **kw: None,
                measure_usage=lambda response: None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_dispatch_denies_closed_when_execution_not_installed(monkeypatch):
    captured, store = _patch_dispatch(monkeypatch, factory=None)

    class Orchestrator:
        async def execute_phase(self, task_id, phase, **options):
            pytest.fail('the phase must not start without an installed gateway factory')

    with pytest.raises(AdmissionDenied, match='not installed'):
        import asyncio
        asyncio.run(tenant_jobs.dispatch_phase(store, Orchestrator(), 'task-1', 'needs'))


def test_dispatch_supplies_job_bound_gateway_to_the_runner(monkeypatch):
    gateway = _gateway()
    calls = []
    factory = lambda capability, job_id: (calls.append((capability, job_id)), gateway)[1]
    captured, store = _patch_dispatch(monkeypatch, factory)

    seen = {}

    class Orchestrator:
        async def execute_phase(self, task_id, phase, **options):
            # What runner.run does at src/delivery_api/runner.py:93.
            seen['gateway'] = current_worker_gateway(store, task_id)

    import asyncio
    job_id = asyncio.run(tenant_jobs.dispatch_phase(store, Orchestrator(), 'task-1', 'needs'))
    assert job_id == 'job-1'
    assert seen['gateway'] is gateway
    assert calls and calls[0][1] == 'job-1'


def test_dispatch_gateway_without_runner_authorization_still_denies(monkeypatch):
    # The deepseek route's gateway carries retail_rates; the subprocess path
    # stays held until exact retail compute metering is installed.
    gateway = _gateway(retail_rates={'unit': 1})
    factory = lambda capability, job_id: gateway
    captured, store = _patch_dispatch(monkeypatch, factory)

    class Orchestrator:
        async def execute_phase(self, task_id, phase, **options):
            current_worker_gateway(store, task_id)

    import asyncio
    with pytest.raises(AdmissionDenied, match='retail subprocess compute metering'):
        asyncio.run(tenant_jobs.dispatch_phase(store, Orchestrator(), 'task-1', 'needs'))
