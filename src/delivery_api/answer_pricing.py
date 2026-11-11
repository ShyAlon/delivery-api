"""Pure answer pricing, not runtime activation. Credits are debited before work.

The fixed start price is an estimate, never an allowance or maximum. Freeze one
snapshot for an answer, then settle actual minus the estimate without a cap.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from .retail_pricing import RetailRates, UNITS_PER_CREDIT, decimal_value

START_CREDITS_SETTING='DELIVERY_ANSWER_START_CREDITS'
DEFAULT_START_CREDITS='0.1'  # Owner decision, 2026-11-11. Overruns are not blocked.
MAX_UNITS=2**63-1


@dataclass(frozen=True)
class AnswerSettlement:
    actual_units:int
    correction_units:int  # Positive debits, negative refunds. Never add E again.
    remainder:Decimal
    overrun:bool
    units_per_credit:int=UNITS_PER_CREDIT


@dataclass(frozen=True)
class AnswerPricing:
    start_credits:Decimal
    rates:RetailRates

    def __post_init__(self):
        value=decimal_value(self.start_credits,START_CREDITS_SETTING)
        with localcontext() as context:
            context.prec=80
            units=value*UNITS_PER_CREDIT
        if value<=0 or units!=units.to_integral_value() or units>MAX_UNITS:
            raise ValueError('answer start credits must be positive exact account units in bigint range')
        if not isinstance(self.rates,RetailRates):raise ValueError('frozen retail rates required')
        object.__setattr__(self,'start_credits',value)

    @classmethod
    def from_environment(cls,env):
        return cls(env.get(START_CREDITS_SETTING,DEFAULT_START_CREDITS),RetailRates.from_environment(env))

    @property
    def estimate_units(self):
        with localcontext() as context:
            context.prec=80
            return int(self.start_credits*UNITS_PER_CREDIT)

    def snapshot(self):
        return {'version':'answer-v1','start_credits':str(self.start_credits),
                'estimate_units':self.estimate_units,'rates':self.rates.snapshot()}

    def settle(self,*,input_tokens,output_tokens,deterministic_ms,remainder=Decimal(0)):
        if any(type(n) is not int or n<0 for n in (input_tokens,output_tokens)):
            raise ValueError('exact nonnegative token counts required')
        with localcontext() as context:
            context.prec=80
            units,left=self.rates.split_units(input_tokens+output_tokens,deterministic_ms,remainder)
        if units>MAX_UNITS:raise ValueError('actual units outside account integer range')
        return AnswerSettlement(units,units-self.estimate_units,left,units>self.estimate_units)
