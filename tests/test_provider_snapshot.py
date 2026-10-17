from datetime import datetime
import pytest
from delivery_api.provider_snapshot import ProviderSnapshot,RULE
from delivery_api.attempt_ledger import AdmissionDenied


def fixture():
    return {'model':'fixture','model_version':'fixture','parser_version':'deepseek-html-v2','published_period_rule':RULE,'fetched_at':'2026-10-10T10:00:00Z','sources':[{'url':'https://api-docs.deepseek.com/one','sha256':'a'*64},{'url':'https://api-docs.deepseek.com/two','sha256':'b'*64}],'published_usd_per_million':{'peak':{'cache_hit':'0.006','cache_miss':'.3','output':'1.2'},'off_peak':{'cache_hit':'.003','cache_miss':'.15','output':'.6'}}}


def test_weekend_uniform_interval_uses_actual_offpeak_not_ceiling():
    s=ProviderSnapshot(fixture());s.fresh('2026-10-10T10:01:00Z')
    band,r=s.billed_rates('2026-10-10T10:00:00Z','2026-10-10T11:00:00Z')
    assert band=='off_peak' and r['output']==600000 and s.ceiling()['output']==1200000


def test_unknown_holiday_bills_peak_and_a_boundary_bills_peak():
    s=ProviderSnapshot(fixture())
    assert s.band('2026-10-12T01:00:00Z')=='peak' and s.band('2026-10-12T05:00:00Z')=='off_peak' and s.band('2026-10-11T07:00:00Z')=='off_peak'
    assert s.billed_rates('2026-10-12T00:59:59Z','2026-10-12T01:00:01Z')[0]=='peak'
    assert s.billed_rates('2026-10-12T01:00:01Z','2026-10-12T03:59:59Z')[0]=='peak'
    assert s.billed_rates('2026-10-12T10:00:00Z','2026-10-12T11:00:00Z')[0]=='off_peak'
    d=fixture();d['holiday_calendar']={'source':'fixture','sha256':'c'*64,'dates':{'2026-10-12':True,'2026-10-13':False}}
    s=ProviderSnapshot(d)
    assert s.band('2026-10-12T01:00:00Z')=='off_peak' and s.band('2026-10-13T01:00:00Z')=='peak' and s.band('2026-10-14T01:00:00Z')=='peak'


def test_stale_hash_drift_and_rule_change_stop():
    s=ProviderSnapshot(fixture())
    s.fresh('2026-10-11T10:00:01Z')   # an old pin is a warning elsewhere, never a stop
    with pytest.raises(AdmissionDenied):s.fresh('2026-10-10T09:59:00Z')   # dated in the future
    d=fixture();d['published_period_rule']='weekdays'
    with pytest.raises(AdmissionDenied):ProviderSnapshot(d)
    d=fixture();d['published_usd_per_million']['peak']['output']='2'
    assert ProviderSnapshot(d).id!=s.id
