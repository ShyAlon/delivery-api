import pytest
from private_adapters import SandboxPurchaseAdapter


class Transport:
    def __init__(self,statuses):self.statuses=list(statuses);self.calls=[]
    def request(self,method,path,*args):
        self.calls.append((method,path));return {'status':self.statuses.pop(0) if len(self.statuses)>1 else self.statuses[0]}


def adapter(statuses):
    a=SandboxPurchaseAdapter.__new__(SandboxPurchaseAdapter)
    class Processor:pass
    a.processor=Processor();a.processor.transport=Transport(statuses);return a


def test_waits_read_only_until_paypal_reports_approved(monkeypatch):
    monkeypatch.setenv('ITEM6_APPROVAL_POLL_SECONDS','0')
    a=adapter(['CREATED','CREATED','APPROVED'])
    assert a.await_approval('ORDER1')=='APPROVED'
    assert all(method=='GET' for method,_ in a.processor.transport.calls) and len(a.processor.transport.calls)==3


def test_a_missing_approval_is_named_not_retried(monkeypatch):
    monkeypatch.setenv('ITEM6_APPROVAL_POLL_SECONDS','0');monkeypatch.setenv('ITEM6_APPROVAL_WAIT_SECONDS','0')
    a=adapter(['CREATED'])
    with pytest.raises(ValueError) as error:a.await_approval('ORDER1')
    assert error.value.safe_check=='order_status=CREATED'
    assert all(method=='GET' for method,_ in a.processor.transport.calls)
