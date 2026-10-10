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
