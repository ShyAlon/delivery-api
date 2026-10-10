"""Exact approved customer formula. Provider expense never sets retail price."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

UNITS_PER_CREDIT = 10**9
DEFAULTS = {'PRICE_PER_TOKEN': '0.0000125', 'PRICE_PER_10MS': '0.0000005', 'USD_PER_CREDIT': '1.00'}


def decimal_value(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label}: finite nonnegative decimal required')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}: finite nonnegative decimal required') from None
    if not result.is_finite() or result < 0:
        raise ValueError(f'{label}: finite nonnegative decimal required')
    return result


@dataclass(frozen=True)
class RetailRates:
    price_per_token: Decimal
    price_per_10ms: Decimal
    usd_per_credit: Decimal

    def __post_init__(self):
        for name in ('price_per_token', 'price_per_10ms', 'usd_per_credit'):
            value = decimal_value(getattr(self, name), name)
            object.__setattr__(self, name, value)
        if self.usd_per_credit <= 0:
            raise ValueError('usd_per_credit: positive decimal required')
        for name in ('price_per_token', 'price_per_10ms'):
            units = getattr(self, name) * UNITS_PER_CREDIT
            if units != units.to_integral_value():
                raise ValueError(f'{name}: rate not representable at 1000000000 units per credit')

    @classmethod
    def from_environment(cls, env: Mapping[str, str]):
        # Omission is valid. A supplied empty/invalid value is not omission.
        return cls(*(env.get(k, default) for k, default in DEFAULTS.items()))

    def components(self, tokens, deterministic_ms=0):
        count = decimal_value(tokens, 'tokens')
        if count != count.to_integral_value():
            raise ValueError('tokens: integer count required')
        duration = decimal_value(deterministic_ms, 'deterministic_ms')
        return count * self.price_per_token, duration / 10 * self.price_per_10ms

    def credits(self, tokens, deterministic_ms=0):
        token, compute = self.components(tokens, deterministic_ms)
        return token + compute

    def split_units(self, tokens, deterministic_ms=0, remainder=Decimal(0)):
        """Pure conversion. Caller persists posting + remainder atomically once."""
        remainder = decimal_value(remainder, 'remainder')
        if remainder >= 1:
            raise ValueError('remainder: fractional unit required')
        exact = self.credits(tokens, deterministic_ms) * UNITS_PER_CREDIT + remainder
        whole = int(exact)
        return whole, exact - whole

    def snapshot(self):
        return {'formula_version': 'tokens-plus-compute-v1', 'units_per_credit': UNITS_PER_CREDIT,
                'price_per_token': str(self.price_per_token), 'price_per_10ms': str(self.price_per_10ms),
                'usd_per_credit': str(self.usd_per_credit)}
