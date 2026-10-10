from decimal import Decimal
import pytest
from delivery_api.compute_meter import ComputeMeter,bind,excluded_wait

def test_wait_union_excludes_nested_provider_and_founder_time():
    now=[0];m=ComputeMeter(lambda:now[0])
    now[0]=1000000;m.pause();now[0]=2000000;m.pause()
    now[0]=3000000;m.resume();now[0]=5000000;m.resume();now[0]=7000000
    assert m.milliseconds()==Decimal(3)

def test_live_wait_is_not_billable_complete():
    m=ComputeMeter(lambda:0);m.pause()
    with pytest.raises(ValueError):m.milliseconds()

def test_founder_exclusion_resumes_after_exception():
    now=[0];m=ComputeMeter(lambda:now[0])
    with bind(m):
        with pytest.raises(RuntimeError),excluded_wait():
            now[0]=1000000;raise RuntimeError()
    now[0]=2000000
    assert m.milliseconds()==1
