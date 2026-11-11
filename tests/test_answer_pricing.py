from decimal import Decimal
import pytest
from delivery_api.answer_pricing import AnswerPricing
from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT


def test_owner_default_and_adjustable_exact_units():
    p=AnswerPricing.from_environment({})
    assert p.estimate_units==100_000_000 and p.start_credits==Decimal('0.1')
    assert AnswerPricing.from_environment({'DELIVERY_ANSWER_START_CREDITS':'0.125'}).estimate_units==125_000_000
    assert p.snapshot()['start_credits']=='0.1'


@pytest.mark.parametrize('value',['','-1','NaN','Infinity',True,'0.0000000001','0', '1000000000000'])
def test_bad_explicit_fixed_debit_never_silently_defaults(value):
    with pytest.raises(ValueError):AnswerPricing.from_environment({'DELIVERY_ANSWER_START_CREDITS':value})


def test_frozen_start_rates_and_delta_not_an_allowance():
    env={};p=AnswerPricing.from_environment(env)
    env['DELIVERY_ANSWER_START_CREDITS']='1';env['PRICE_PER_TOKEN']='1'
    result=p.settle(input_tokens=10000,output_tokens=2000,deterministic_ms=100)
    assert result.actual_units==150_005_000
    assert result.correction_units==50_005_000 and result.overrun is True
    assert p.estimate_units==100_000_000
    refund=p.settle(input_tokens=100,output_tokens=0,deterministic_ms=0)
    assert refund.correction_units==-98_750_000 and not refund.overrun


def test_fractional_account_remainder_is_not_per_call_rounding():
    p=AnswerPricing.from_environment({});r=Decimal('0.7')
    result=p.settle(input_tokens=3,output_tokens=4,deterministic_ms='0.003',remainder=r)
    units,left=RetailRates.from_environment({}).split_units(7,Decimal('0.003'),r)
    assert (result.actual_units,result.remainder)==(units,left)
    assert result.correction_units==units-p.estimate_units
    assert result.units_per_credit==UNITS_PER_CREDIT


@pytest.mark.parametrize('args',[{'input_tokens':True},{'input_tokens':-1},{'input_tokens':1.5},{'output_tokens':-1},{'deterministic_ms':'NaN'},{'remainder':1},{'remainder':-1}])
def test_invalid_usage_is_not_zero_actual(args):
    with pytest.raises(ValueError):AnswerPricing.from_environment({}).settle(**({'input_tokens':0,'output_tokens':0,'deterministic_ms':0}|args))
