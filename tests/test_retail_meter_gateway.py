from delivery_api.compute_meter import ComputeMeter,bind
from delivery_api.producer_admission import WorkerAdmissionGateway

class Store:
    def __init__(self):self.row={'job_id':'j','state':'reserved'}
    def provider_attempt(self,a):return self.row
    def transition_provider_attempt(self,a,action,*args,**kw):
        self.row['state']={'start':'dispatched','settle':'settled','uncertain':'uncertain'}[action]
        return self.row

def test_distinct_interview_gateway_uses_same_bound_meter():
    now=[0];m=ComputeMeter(lambda:now[0]);g=WorkerAdmissionGateway(Store(),'j',measure_usage=lambda r:r)
    with bind(m):
        now[0]=1000000;g.start('a');now[0]=9000000;g.settle('a',{})
        now[0]=10000000
    assert m.milliseconds()==2


def test_changed_source_holds_accepted_usage_and_finishes_meter_pause():
    import pytest
    from delivery_api.attempt_ledger import AdmissionDenied
    now=[0];m=ComputeMeter(lambda:now[0]);s=Store()
    change=[False]
    def verify():
        if change[0]:raise ValueError('changed')
    g=WorkerAdmissionGateway(s,'j',measure_usage=lambda r:r,verify_provider_snapshot=verify)
    with bind(m):
        g.start('a');change[0]=True;now[0]=1000000
        with pytest.raises(AdmissionDenied,match='held'):g.settle('a',{})
    assert s.row['state']=='uncertain' and m.depth==0


def test_product_phase_selection_not_env_model_is_the_authority():
    import pytest
    from delivery_api.attempt_ledger import AdmissionDenied
    s=Store();s.worker_job=lambda j:{'execution_options':{'model_route':'deepseek:selected-phase-model'}}
    g=WorkerAdmissionGateway(s,'j',resolve_quote=lambda **kw: (_ for _ in ()).throw(AssertionError('must not reach resolver')))
    with pytest.raises(AdmissionDenied,match='phase selection'):
        g.reserve(attempt_id='a',stage='needs',provider='deepseek',model='env-model',messages=[],max_output_tokens=1)


def test_exact_retail_subprocess_cannot_omit_compute_meter():
    import pytest
    from delivery_api.producer_admission import worker_gateway,current_worker_gateway
    from delivery_api.retail_pricing import RetailRates
    from delivery_api.attempt_ledger import AdmissionDenied
    s=Store();s.worker_job=lambda j:{'task_id':'t','state':'running'}
    g=WorkerAdmissionGateway(s,'j',retail_rates=RetailRates.from_environment({}))
    with worker_gateway(g),pytest.raises(AdmissionDenied,match='subprocess compute'):
        current_worker_gateway(s,'t')
