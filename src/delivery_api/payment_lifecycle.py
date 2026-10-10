"""Pure payment lifecycle preparation. No verifier, balance writes or routing.

Persistence must apply these transitions with unique purchase/payment bindings
and an atomic credit ledger. A browser return is never a payment event.
"""
from dataclasses import dataclass,replace
from .payment_contract import PurchaseIntent,ProcessorEvidence,prepare_fulfillment


@dataclass(frozen=True)
class PurchaseState:
    intent:PurchaseIntent
    status:str='awaiting_payment'
    payment_key:str|None=None
    credit_posted:bool=False
    review_required:bool=False

    def __post_init__(self):
        if not isinstance(self.intent,PurchaseIntent):
            raise ValueError('immutable purchase intent required')
        if type(self.credit_posted) is not bool or type(self.review_required) is not bool:
            raise ValueError('explicit persisted lifecycle flags required')
        expected='operator_review' if self.review_required else ('fulfilled' if self.credit_posted else 'awaiting_payment')
        if self.status!=expected:
            raise ValueError('inconsistent persisted lifecycle status')
        if self.credit_posted and not self.payment_key:
            raise ValueError('posted credit must bind payment')
        if self.payment_key is not None and (not isinstance(self.payment_key,str) or len(self.payment_key)!=64 or any(c not in '0123456789abcdef' for c in self.payment_key)):
            raise ValueError('canonical payment key required')


def paid_transition(state,evidence,*,processor,merchant,live_mode):
    if not isinstance(state,PurchaseState):raise ValueError('persisted purchase state required')
    plan=prepare_fulfillment(state.intent,evidence,expected_processor=processor,
                             expected_merchant=merchant,expected_live_mode=live_mode)
    if state.payment_key and state.payment_key!=plan['payment_key']:
        raise ValueError('different payment cannot fulfill the same purchase')
    if state.review_required:
        return state,{'credit_delta':0,'reason':'review_required'}
    if state.credit_posted:
        return state,{'credit_delta':0,'reason':'idempotent_paid_replay'}
    return replace(state,status='fulfilled',payment_key=plan['payment_key'],credit_posted=True),{
        'credit_delta':state.intent.credit_units,'units_per_credit':state.intent.units_per_credit,
        'owner_id':state.intent.owner_id,'payment_key':plan['payment_key'],
        'reason':'confirmed_payment_plan','effect_executed':False}


def adverse_transition(state,*,purchase_id,intent_digest,event_kind):
    if not isinstance(state,PurchaseState) or purchase_id!=state.intent.purchase_id or intent_digest!=state.intent.digest:
        raise ValueError('verified adverse event must bind purchase')
    if event_kind not in ('refund','reversal','dispute'):
        raise ValueError('verified adverse payment event required')
    # No automatic refund, credit removal or re-grant. A later paid callback
    # must not reopen a purchase whose adverse event arrived first.
    return replace(state,status='operator_review',review_required=True),{
        'credit_delta':0,'reason':event_kind+'_requires_review','effect_executed':False}
