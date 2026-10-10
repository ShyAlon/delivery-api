"""Provider-neutral payment evidence preparation, never payment authorization.

No checkout, webhook verifier, credit posting or production offer defaults.
A typed evidence object still needs authenticated processor verification at the
integration boundary. Merely constructing it proves nothing.
"""
from dataclasses import dataclass,asdict
from uuid import UUID
import hashlib,json,re

MAX_BIGINT=9223372036854775807


def _key(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',value))


def _positive(value):
    return type(value) is int and 0<value<=MAX_BIGINT


@dataclass(frozen=True)
class ProcessorBinding:
    processor:str
    merchant_id:str
    live_mode:bool

    def __post_init__(self):
        if not _key(self.processor) or not _key(self.merchant_id) or type(self.live_mode) is not bool:
            raise ValueError('explicit processor merchant and mode required')


@dataclass(frozen=True)
class PurchaseIntent:
    purchase_id:str
    owner_id:str
    offer_version:str
    offer_id:str
    currency:str
    amount_minor:int
    credit_units:int
    units_per_credit:int

    def __post_init__(self):
        if any(not _key(v) for v in (self.purchase_id,self.offer_version,self.offer_id)):
            raise ValueError('immutable purchase and reviewed offer identities required')
        try:canonical=str(UUID(self.owner_id))
        except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical owner required') from exc
        if canonical!=self.owner_id:raise ValueError('canonical owner required')
        if not isinstance(self.currency,str) or not re.fullmatch(r'[A-Z]{3}',self.currency):
            raise ValueError('explicit uppercase currency required')
        if any(not _positive(v) for v in (self.amount_minor,self.credit_units,self.units_per_credit)):
            raise ValueError('positive bounded integer amount and credit scale required')

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class ProcessorEvidence:
    processor:str
    merchant_account:str
    event_id:str
    payment_id:str
    purchase_id:str
    intent_digest:str
    currency:str
    amount_minor:int
    status:str
    live_mode:bool
    processor_fee_minor:int|None=None
    net_received_minor:int|None=None


def prepare_fulfillment(intent,evidence,*,expected_processor,expected_merchant,expected_live_mode):
    """Validate fields from an independently verified processor adapter.

    This result is a plan, not a grant. Storage must enforce one payment ID and
    purchase ID credit posting atomically. Browser/LLM data is never this input.
    """
    if not isinstance(intent,PurchaseIntent) or not isinstance(evidence,ProcessorEvidence):
        raise ValueError('typed intent and verified-adapter evidence required')
    if not _key(expected_processor) or not _key(expected_merchant) or type(expected_live_mode) is not bool:
        raise ValueError('explicit processor, merchant and mode required')
    if any(not _key(v) for v in (evidence.processor,evidence.merchant_account,evidence.event_id,evidence.payment_id,evidence.purchase_id)):
        raise ValueError('bounded processor evidence identities required')
    if evidence.processor!=expected_processor or evidence.merchant_account!=expected_merchant or type(evidence.live_mode) is not bool or evidence.live_mode!=expected_live_mode:
        raise ValueError('processor account or test/live mode mismatch')
    if evidence.purchase_id!=intent.purchase_id or evidence.intent_digest!=intent.digest:
        raise ValueError('processor payment does not bind immutable purchase')
    if type(evidence.amount_minor) is not int or evidence.amount_minor!=intent.amount_minor or evidence.currency!=intent.currency:
        raise ValueError('confirmed amount or currency mismatch')
    if evidence.status!='paid':raise ValueError('confirmed paid state required; other states need review')
    fee=evidence.processor_fee_minor;net=evidence.net_received_minor
    if (fee is None)!=(net is None):raise ValueError('complete gross/fee/net evidence required')
    if fee is not None and (type(fee) is not int or type(net) is not int or fee<0 or net<0 or fee+net!=intent.amount_minor):
        raise ValueError('exact matching gross/fee/net amounts required')
    identity={'processor':evidence.processor,'merchant_account':evidence.merchant_account,
              'payment_id':evidence.payment_id,'live_mode':evidence.live_mode}
    return {'purchase_id':intent.purchase_id,'owner_id':intent.owner_id,
            'credit_units':intent.credit_units,'units_per_credit':intent.units_per_credit,
            'intent_digest':intent.digest,'event_id':evidence.event_id,
            'payment_key':hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'processor_fee_minor':fee,'net_received_minor':net,
            'effect_executed':False,'commit_available':False}
