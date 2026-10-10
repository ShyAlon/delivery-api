from decimal import Decimal
import pytest
from delivery_api.retail_pricing import RetailRates, UNITS_PER_CREDIT


def test_optional_defaults_are_the_existing_formula():
    r = RetailRates.from_environment({})
    assert r.credits(400000) == Decimal('5')
    assert r.credits(1,10) == Decimal('0.0000130')
    assert r.split_units(1,1) == (12550, Decimal('0'))
    assert r.snapshot()['units_per_credit'] == UNITS_PER_CREDIT


def test_optional_overrides_independently():
    r = RetailRates.from_environment({'PRICE_PER_10MS': '0'})
    assert r.credits(400000,1000) == 5
    assert r.usd_per_credit == 1


@pytest.mark.parametrize('value', ['', '-1', 'NaN', 'Infinity', '0.00000000001', True])
def test_bad_explicit_rate_is_not_a_default(value):
    with pytest.raises(ValueError):
        RetailRates.from_environment({'PRICE_PER_TOKEN': value})


def test_subunit_compute_carries_without_per_call_rounding():
    r = RetailRates.from_environment({})
    remainder = Decimal(0); posted = 0
    for _ in range(1000):
        units, remainder = r.split_units(0, Decimal('0.0003'), remainder)
        posted += units
    assert (posted,remainder) == r.split_units(0,Decimal('0.3'))
    assert posted == 15


def test_frozen_rates_do_not_follow_later_env_change():
    env = {}; r = RetailRates.from_environment(env)
    env['PRICE_PER_TOKEN'] = '1'
    assert r.credits(1) == Decimal('0.0000125')


@pytest.mark.parametrize('tokens,ms',[(1.5,0),(-1,0),(0,-1),(0,'NaN')])
def test_invalid_usage_rejected(tokens,ms):
    with pytest.raises(ValueError):
        RetailRates.from_environment({}).credits(tokens,ms)
