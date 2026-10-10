from dataclasses import replace
import pytest
from delivery_api.payment_contract import PurchaseIntent,ProcessorEvidence,prepare_fulfillment

INTENT=PurchaseIntent('purchase-test','00000000-0000-0000-0000-000000000001','fictional-v1','fictional-pack','USD',123,456,1000)
EVIDENCE=ProcessorEvidence('fictional','merchant-test','event-1','payment-1',INTENT.purchase_id,INTENT.digest,'USD',123,'paid',False)


def prepare(intent=INTENT,evidence=EVIDENCE):
    return prepare_fulfillment(intent,evidence,expected_processor='fictional',expected_merchant='merchant-test',expected_live_mode=False)


def test_exact_binding_and_duplicate_event_identity_are_payment_scoped():
    first=prepare();second=prepare(evidence=replace(EVIDENCE,event_id='event-2'))
    assert first['payment_key']==second['payment_key']
    assert first['owner_id']==INTENT.owner_id and first['credit_units']==456
    assert not first['effect_executed'] and not first['commit_available']
    assert replace(INTENT,credit_units=457).digest!=INTENT.digest


@pytest.mark.parametrize('field,value',[
 ('processor','different'),('merchant_account','other'),('live_mode',True),('live_mode',0),
 ('purchase_id','different'),('intent_digest','0'*64),('currency','EUR'),('amount_minor',124),
 ('amount_minor',123.0),('amount_minor',True),('status','pending'),('status','refunded'),
 ('status','disputed'),('status','browser_success'),('payment_id',''),('event_id','x\n')])
def test_unverified_conflicting_unpaid_or_wrong_account_evidence_cannot_credit(field,value):
    with pytest.raises(ValueError):prepare(evidence=replace(EVIDENCE,**{field:value}))


@pytest.mark.parametrize('field,value',[
 ('owner_id','../foreign'),('owner_id','00000000-0000-0000-0000-00000000000A'),
 ('purchase_id','../path'),('offer_id',''),('offer_version',''),('currency','usd'),
 ('amount_minor',0),('credit_units',-1),('units_per_credit',True),('credit_units',2**63)])
def test_no_guessed_float_negative_overflow_or_owner_alias_intents(field,value):
    with pytest.raises(ValueError):replace(INTENT,**{field:value})


def test_plain_customer_json_is_not_processor_evidence():
    with pytest.raises(ValueError):prepare(evidence={'status':'paid','credits':999999})


@pytest.mark.parametrize('fee,net',[(1,None),(None,1),(-1,124),(True,122),(1,122.0),(1,100)])
def test_invalid_actual_fee_net_evidence_rejected(fee,net):
    with pytest.raises(ValueError):prepare(evidence=replace(EVIDENCE,processor_fee_minor=fee,net_received_minor=net))
