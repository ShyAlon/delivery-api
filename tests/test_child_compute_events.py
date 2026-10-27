from decimal import Decimal
import pytest
from delivery_api.child_compute_events import ChildComputeEvents,ComputeEvidenceError


def event(seq,at,kind,wait_id=None):
    value={'sequence':seq,'monotonic_ns':at,'kind':kind}
    if wait_id is not None:value['wait_id']=wait_id
    return value


def test_nested_provider_and_founder_wait_union_matches_inprocess():
    meter=ChildComputeEvents()
    for e in [event(1,0,'start'),event(2,10_000_000,'wait.begin','provider:a'),
              event(3,20_000_000,'wait.begin','founder:b'),event(4,30_000_000,'wait.end','provider:a'),
              event(5,40_000_000,'wait.end','founder:b'),event(6,50_000_000,'finish')]:meter.accept(e)
    assert meter.milliseconds()==Decimal(20)


@pytest.mark.parametrize('bad',[event(3,1,'finish'),event(2,-1,'finish'),event(2,True,'finish'),event(2,2,'wait.end','absent'),event(2,2,'start')])
def test_missing_or_ambiguous_boundaries_never_price(bad):
    meter=ChildComputeEvents();meter.accept(event(1,0,'start'))
    with pytest.raises(ComputeEvidenceError):meter.accept(bad)
    with pytest.raises(ComputeEvidenceError):meter.milliseconds()


def test_open_wait_and_crash_have_no_guessed_cost():
    meter=ChildComputeEvents();meter.accept(event(1,0,'start'));meter.accept(event(2,10,'wait.begin','provider:a'))
    with pytest.raises(ComputeEvidenceError):meter.accept(event(3,20,'finish'))
    with pytest.raises(ComputeEvidenceError):meter.milliseconds()


def test_duplicate_wait_id_or_events_after_finish_poison_evidence():
    meter=ChildComputeEvents();meter.accept(event(1,0,'start'));meter.accept(event(2,10,'wait.begin','a'))
    with pytest.raises(ComputeEvidenceError):meter.accept(event(3,11,'wait.begin','a'))
    clean=ChildComputeEvents();clean.accept(event(1,0,'start'));clean.accept(event(2,20,'finish'))
    with pytest.raises(ComputeEvidenceError):clean.accept(event(3,21,'finish'))
    with pytest.raises(ComputeEvidenceError):clean.milliseconds()
