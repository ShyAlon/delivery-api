from datetime import datetime,timezone,timedelta
import pytest
from delivery_api.provider_balance import validate_balance
from delivery_api.attempt_ledger import AdmissionDenied

def evidence(currency='USD',total='1'):
    return {'response':{'is_available':True,'balance_infos':[{'currency':currency,'total_balance':total,'granted_balance':'0','topped_up_balance':total}]},'observed_at':datetime.now(timezone.utc).isoformat()}

def test_usd_estimate_check_is_not_atomic_hold():
    assert validate_balance(evidence(),100,1000)['atomic_hold'] is False
    with pytest.raises(AdmissionDenied):validate_balance(evidence(total='.01'),100,1000)

def test_cny_does_not_invent_exchange_rate():
    assert 'no USD' in validate_balance(evidence('CNY'),100,1000)['check']

def test_unavailable_and_stale_stop():
    e=evidence();e['response']['is_available']=False
    with pytest.raises(AdmissionDenied):validate_balance(e,1,1000)
    e=evidence();e['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    with pytest.raises(AdmissionDenied):validate_balance(e,1,1000)

def test_start_verifies_source_before_balance_read_and_logs_failure_age(caplog):
    from delivery_api.producer_admission import WorkerAdmissionGateway
    order=[]
    class Store:
        def provider_attempt(self,a):return {'job_id':'j','quote':{'estimate_policy':1}}
        def record_provider_balance_check(self,a,b):order.append('record')
        def transition_provider_attempt(self,a,action,usage=None,*,require_prepaid=False):order.append('start')
    g=WorkerAdmissionGateway(Store(),'j',provider_balance=lambda:order.append('read'),verify_provider_snapshot=lambda:order.append('source'))
    g.start('a')
    assert order==['source','read','record','start']
    e=evidence();e['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    with caplog.at_level('WARNING'),pytest.raises(AdmissionDenied):validate_balance(e,1,1000)
    assert 'age_seconds=6' in caplog.text
