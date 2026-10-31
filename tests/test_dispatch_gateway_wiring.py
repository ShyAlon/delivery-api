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
from delivery_api.producer_admission import current_worker_gateway,WorkerAdmissionGateway


def _real_gateway(**kw):
    from delivery_api.producer_admission import WorkerAdmissionGateway
    return WorkerAdmissionGateway(_Store(), 'job-1', **kw)


class _Store:
    def worker_job(self, job_id):
        return {'task_id': 't1', 'state': 'running', 'job_id': job_id}


def test_current_worker_gateway_accepts_a_complete_serialized_protocol_pair():
    from delivery_api.producer_admission import current_worker_gateway,WorkerAdmissionGateway, worker_gateway
    gw = _real_gateway(resolve_serialized=lambda *a: None, measure_raw_usage=lambda *a: None)
    with worker_gateway(gw):
        assert current_worker_gateway(_Store(), 't1') is gw


def test_current_worker_gateway_refuses_incomplete_pairs_before_any_provider_contact():
    from delivery_api.attempt_ledger import AdmissionDenied
    from delivery_api.producer_admission import current_worker_gateway,WorkerAdmissionGateway, worker_gateway
    for kw in ({'resolve_serialized': lambda *a: None},          # serialized without usage
               {'measure_raw_usage': lambda *a: None},          # usage without resolution
               {'resolve_quote': lambda *a: None},              # message-shaped without usage
               {'measure_usage': lambda *a: None}):             # usage without resolution
        with worker_gateway(_real_gateway(**kw)):
            with pytest.raises(AdmissionDenied, match='bounds/pricing/usage unavailable'):
                current_worker_gateway(_Store(), 't1')


def test_current_worker_gateway_accepts_the_complete_message_shaped_pair():
    from delivery_api.producer_admission import current_worker_gateway,WorkerAdmissionGateway, worker_gateway
    gw = _real_gateway(resolve_quote=lambda *a: None, measure_usage=lambda *a: None)
    with worker_gateway(gw):
        assert current_worker_gateway(_Store(), 't1') is gw


def test_product_phase_selection_not_env_model_is_the_authority():
    import pytest
    from delivery_api.attempt_ledger import AdmissionDenied
    s=_Store();s.worker_job=lambda j:{'execution_options':{'model_route':'deepseek:selected-phase-model'}}
    g=WorkerAdmissionGateway(s,'j',resolve_quote=lambda **kw: (_ for _ in ()).throw(AssertionError('must not reach resolver')))
    with pytest.raises(AdmissionDenied,match='phase selection'):
        g.reserve(attempt_id='a',stage='needs',provider='deepseek',model='env-model',messages=[],max_output_tokens=1)


def test_exact_retail_subprocess_cannot_omit_compute_meter():
    import pytest
    from delivery_api.producer_admission import worker_gateway,current_worker_gateway
    from delivery_api.retail_pricing import RetailRates
    from delivery_api.attempt_ledger import AdmissionDenied
    s=_Store();s.worker_job=lambda j:{'task_id':'t','state':'running'}
    g=WorkerAdmissionGateway(s,'j',retail_rates=RetailRates.from_environment({}))
    with worker_gateway(g),pytest.raises(AdmissionDenied,match='subprocess compute'):
        current_worker_gateway(s,'t')


def test_removed_dispatch_default_refuses_before_queue_or_work(monkeypatch):
    import asyncio
    from delivery_api import config,inprocess_engine
    monkeypatch.setattr(config,'CLOUD_TENANTS',True);monkeypatch.setattr(inprocess_engine,'_engine',None)
    def forbidden(*a,**k):pytest.fail('no queue or provider side effect')
    store=SimpleNamespace(enqueue_worker=forbidden)
    with pytest.raises(AdmissionDenied,match='child execution is not installed'):
        asyncio.run(tenant_jobs.dispatch_phase(store,SimpleNamespace(execute_phase=forbidden),'task','needs'))
