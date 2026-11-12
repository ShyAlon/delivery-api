"""No paid requests: eliminate default-model discovery on an admitted task pin."""
from delivery_api.child_paid_runtime import PaidChildRuntime
import pytest


def test_retained_task_pin_requires_no_default_catalog_read():
    runtime=PaidChildRuntime(models={'deepseek':'initial'},api_key='fixture')
    class Pins:
        @property
        def models(self):raise AssertionError('retained task must not rediscover global defaults')
        def accepts(self,provider,model):return (provider,model)==('deepseek','retained')
    runtime.pin_factory=Pins()
    runtime.require('deepseek','retained')
    endpoint,headers=runtime.transport('deepseek','retained','unused')
    assert endpoint.startswith('https://') and headers


def test_unknown_model_still_requires_reviewed_default_and_refuses_mismatch():
    runtime=PaidChildRuntime(models={'deepseek':'initial'},api_key='fixture')
    class Pins:
        models={'deepseek':'current'}
        def accepts(self,*args):return False
    runtime.pin_factory=Pins()
    runtime.require('deepseek','current')
    with pytest.raises(PermissionError):runtime.require('deepseek','unreviewed')
