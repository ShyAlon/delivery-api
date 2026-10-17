from datetime import datetime
import pytest
from delivery_api.billing_band import BandRates,peak_at,select_rates
from delivery_api.attempt_ledger import AdmissionDenied

def d(value):return datetime.fromisoformat(value+'+00:00')
@pytest.mark.parametrize('stamp,expected',[
 ('2026-10-09T00:59:59',False),('2026-10-09T01:00:00',True),
 ('2026-10-09T03:59:59',True),('2026-10-09T04:00:00',False),
 ('2026-10-09T06:00:00',True),('2026-10-09T10:00:00',False),
 ('2026-10-10T06:00:00',False)])
def test_utc_weekday_windows(stamp,expected):assert peak_at(d(stamp)) is expected

def test_ambiguous_expense_and_retail_are_separate_and_audited():
 out=select_rates(accepted_at=d('2026-10-09T03:59:50'),completed_at=d('2026-10-09T04:00:10'),expense=BandRates(30,15),retail=BandRates(80,40))
 assert out['expense_rate']==30 and out['retail_rate']==40 and out['billing_band_ambiguous']
 assert out['expense_measurement']=='conservative_peak_estimate'
 assert out['accepted_at'].endswith('+00:00') and out['completed_at'].endswith('+00:00')

def test_same_band_endpoints_do_not_hide_intervening_boundary():
 out=select_rates(accepted_at=d('2026-10-09T00:00:00'),completed_at=d('2026-10-09T05:00:00'),expense=BandRates(30,15),retail=BandRates(80,40))
 assert out['billing_band_ambiguous']

@pytest.mark.parametrize('rule,rates',[('acceptance',(30,80)),('completion',(15,40))])
def test_clarified_rule_is_explicit_config(rule,rates):
 out=select_rates(accepted_at=d('2026-10-09T03:59:50'),completed_at=d('2026-10-09T04:00:10'),expense=BandRates(30,15),retail=BandRates(80,40),provider_rule=rule)
 assert not out['billing_band_ambiguous'] and (out['expense_rate'],out['retail_rate'])==rates

def test_unknown_same_band_can_use_reviewed_band():
 out=select_rates(accepted_at=d('2026-10-10T03:00:00'),completed_at=d('2026-10-10T05:00:00'),expense=BandRates(30,15),retail=BandRates(80,40))
 assert not out['billing_band_ambiguous'] and out['expense_rate']==15

def test_naive_unordered_or_unreviewed_rates_rejected():
 with pytest.raises(AdmissionDenied):peak_at(datetime(2026,10,9))
 with pytest.raises(AdmissionDenied):BandRates(True,0)
 with pytest.raises(AdmissionDenied):select_rates(accepted_at=d('2026-10-09T04:00:00'),completed_at=d('2026-10-09T03:00:00'),expense=BandRates(30,15),retail=BandRates(80,40))
