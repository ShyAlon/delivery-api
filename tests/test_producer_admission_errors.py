import pytest
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.producer_admission import WorkerAdmissionGateway


class Store:
    def __init__(self):self.calls=[]
    def provider_attempt(self,attempt):return {'job_id':'J','quote':{'estimate_policy':{}}}
    def record_provider_balance_check(self,*a):self.calls.append('balance')
    def transition_provider_attempt(self,*a,**k):self.calls.append(a[1] if len(a)>1 else k.get('action'))


def gateway(verify=None,balance=None):
    return WorkerAdmissionGateway(Store(),'J',verify_provider_snapshot=verify,provider_balance=balance)


def test_a_failed_vendor_check_is_named_as_an_admission_refusal_with_its_message():
    def verify():raise ValueError('vendor source changed during run: pinned terms differ (published_usd_per_million)')
    g=gateway(verify=verify,balance=lambda:{})
    with pytest.raises(AdmissionDenied,match='vendor source check failed: .*published_usd_per_million') as info:g.start('a')
    # The refusal is provably pre-dispatch, so the reservation is released; nothing is started.
    assert type(info.value.__cause__) is ValueError and g.store.calls==['cancel']


def test_a_failed_balance_read_is_named_and_nothing_is_started():
    def balance():raise ValueError('provider balance read unavailable')
    g=gateway(verify=lambda:True,balance=balance)
    with pytest.raises(AdmissionDenied,match='provider balance read failed: provider balance read unavailable'):g.start('a')
    assert g.store.calls==['cancel']


def test_an_admission_refusal_keeps_its_own_message():
    def verify():raise AdmissionDenied('vendor snapshot stale/future')
    g=gateway(verify=verify,balance=lambda:{})
    with pytest.raises(AdmissionDenied,match='^vendor snapshot stale/future$'):g.start('a')
    assert g.store.calls==['cancel']
