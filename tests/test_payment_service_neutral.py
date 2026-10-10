"""A non-PayPal stub uses the same purchase and credit service unchanged."""
from delivery_api.payment_service import PaymentService
from delivery_api.payment_contract import ProcessorBinding,ProcessorEvidence
from test_payment_contract import INTENT


def test_processor_swap_does_not_require_paypal_events_or_business_logic():
    class Adapter:
        binding=ProcessorBinding('alternative','alternative-merchant',False)
        replay_window_seconds=600
        def completed_evidence(self,intent,order_id,**kwargs):
            return ProcessorEvidence('alternative','alternative-merchant','neutral-event','neutral-payment',intent.purchase_id,intent.digest,'USD',123,'paid',False,3,120)
    class Repo:
        def get_purchase(self,id):return {'intent':INTENT,'order_id':'neutral-order'}
        def fulfill(self,intent,evidence,plan,receipt=None):
            assert plan['credit_units']==456 and plan['processor_fee_minor']==3
            return {'state':'fulfilled','credit_posted':True}
    assert PaymentService(Adapter(),Repo()).capture(INTENT.purchase_id)['credit_posted']
