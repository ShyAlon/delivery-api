from dataclasses import replace
import pytest
from delivery_api.payment_lifecycle import PurchaseState,paid_transition,adverse_transition
from test_payment_contract import INTENT,EVIDENCE


def paid(state,evidence=EVIDENCE):
    return paid_transition(state,evidence,processor='fictional',merchant='merchant-test',live_mode=False)


def adverse(state,kind):
    return adverse_transition(state,purchase_id=INTENT.purchase_id,intent_digest=INTENT.digest,event_kind=kind)


def test_two_completion_paths_and_many_callbacks_credit_once():
    state=PurchaseState(INTENT);total=0
    for i in range(50):
        state,plan=paid(state,replace(EVIDENCE,event_id='event-'+str(i)))
        total+=plan['credit_delta']
    assert state.credit_posted and total==INTENT.credit_units


@pytest.mark.parametrize('kind',['refund','reversal','dispute'])
def test_adverse_event_first_blocks_late_paid_and_preserves_no_charge(kind):
    state,plan=adverse(PurchaseState(INTENT),kind)
    assert plan['credit_delta']==0 and state.review_required
    state,plan=paid(state)
    assert plan['credit_delta']==0 and not state.credit_posted


@pytest.mark.parametrize('kind',['refund','reversal','dispute'])
def test_adverse_event_after_fulfillment_never_automatically_debits_or_regrants(kind):
    state,_=paid(PurchaseState(INTENT))
    state,plan=adverse(state,kind)
    assert state.credit_posted and state.review_required and plan['credit_delta']==0
    state,plan=paid(state)
    assert plan['credit_delta']==0


def test_distinct_capture_cannot_credit_one_purchase_twice():
    state,_=paid(PurchaseState(INTENT))
    with pytest.raises(ValueError):paid(state,replace(EVIDENCE,payment_id='different-payment'))


def test_foreign_adverse_event_never_changes_purchase():
    with pytest.raises(ValueError):adverse_transition(PurchaseState(INTENT),purchase_id='foreign',intent_digest=INTENT.digest,event_kind='refund')


@pytest.mark.parametrize('fields',[
 {'intent':{}},{'credit_posted':1},{'review_required':1},{'status':'fulfilled'},
 {'credit_posted':True,'status':'fulfilled'},{'payment_key':'bad-key'},
 {'review_required':True,'status':'awaiting_payment'}])
def test_impossible_persisted_states_are_rejected(fields):
    with pytest.raises(ValueError):PurchaseState(**({'intent':INTENT}|fields))
