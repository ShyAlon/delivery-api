"""Hermetic checks of the vendor-terms rule (owner ruling 2026-10-17): failed refreshes warn under 24h and block at or above 24h."""
import copy,datetime,hashlib
import pytest
from delivery_api import vendor_sources as vs,vendor_status,cloud_runtime
from delivery_api.provider_snapshot import RULE

RATES={p:{'cache_hit':'0.01','cache_miss':'0.10','output':'0.20'} for p in ('peak','off_peak')}


def snapshot(hours_old=1):
    return {'model':'m','model_version':'v1','parser_version':'deepseek-html-v2','published_period_rule':RULE,
        'fetched_at':(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=hours_old)).isoformat(),
        'sources':[{'url':vs.PRICING_URL,'sha256':'a'*64},{'url':vs.API_URL,'sha256':'b'*64}],
        'published_usd_per_million':copy.deepcopy(RATES),'raw_context_limit':'1M','raw_output_limit':'MAXIMUM: 384K',
        'context_proposal':{'maximum_output_tokens':393216}}


def parsed(**over):
    s=snapshot();out={k:s[k] for k in vs.FACT_KEYS};out['context_proposal']=dict(s['context_proposal']);out.update(over);return out


@pytest.fixture(autouse=True)
def clean():
    vs._GOOD.clear();vs._WARNED.clear();vendor_status.reset();yield;vs._GOOD.clear();vs._WARNED.clear();vendor_status.reset()


def run(snap,monkeypatch,fetcher=lambda u:'page changed',parse=None):
    lines=[]
    if parse is not None:monkeypatch.setattr(vs,'parse',parse)
    return vs.verify_current_sources(snap,fetcher,cached=False,log=lines.append),lines


def test_unreadable_pages_with_a_fresh_pin_warn_and_the_call_continues(monkeypatch):
    def boom(url):raise ConnectionError('no route')
    monkeypatch.setattr(vs,'fetch_verified_source',lambda url,fetcher:boom(url))
    ok,lines=run(snapshot(1),monkeypatch)
    status=vendor_status.status()
    assert ok is True and status['ok'] is False and {w['kind'] for w in status['warnings']}>={'vendor-read'}
    assert lines and 'WARNING vendor terms (vendor-read)' in lines[0] and 'no route' in lines[0]


def test_a_changed_price_is_a_warning_naming_the_term_old_and_new_and_billing_stays_pinned(monkeypatch):
    changed=copy.deepcopy(RATES);changed['peak']['output']='0.40'
    ok,lines=run(snapshot(),monkeypatch,parse=lambda p,a,m:parsed(published_usd_per_million=changed))
    warning=[w for w in vendor_status.status()['warnings'] if w['kind']=='vendor-change'][0]
    assert ok is True and 'published_usd_per_million was' in warning['text'] and "'0.40'" in warning['text'] and 'PINNED' in warning['text']
    assert 'WARNING vendor terms (vendor-change)' in lines[0]


def test_a_changed_output_limit_and_a_changed_peak_rule_are_warnings(monkeypatch):
    ok,_=run(snapshot(),monkeypatch,parse=lambda p,a,m:parsed(published_period_rule='other',context_proposal={'maximum_output_tokens':1000}))
    text=[w for w in vendor_status.status()['warnings'] if w['kind']=='vendor-change'][0]['text']
    assert ok is True and 'published_period_rule' in text and 'maximum_output_tokens was 393216, now 1000' in text


def test_a_page_that_no_longer_parses_is_a_warning_even_when_it_names_a_rate(monkeypatch):
    def bad(p,a,m):raise vs.SourceChanged('explicit two-period rates required')
    ok,_=run(snapshot(),monkeypatch,parse=bad)
    assert ok is True and 'PINNED' in vendor_status.status()['warnings'][0]['text']


def test_identical_terms_pass_clear_every_warning_and_record_the_check(monkeypatch):
    vendor_status.warn('vendor-read','earlier failure',log=lambda t:None)
    ok,_=run(snapshot(),monkeypatch,parse=lambda p,a,m:parsed())
    status=vendor_status.status()
    assert ok is True and status['ok'] is True and status['last_verified_at']


def test_a_warning_persists_until_a_check_succeeds(monkeypatch):
    changed=copy.deepcopy(RATES);changed['peak']['output']='0.40'
    run(snapshot(),monkeypatch,parse=lambda p,a,m:parsed(published_usd_per_million=changed))
    vs._WARNED.clear()
    run(snapshot(),monkeypatch,parse=lambda p,a,m:parsed(published_usd_per_million=changed))
    got=vendor_status.status()
    assert got['ok'] is False and got['warnings'][0]['count']==2


def test_the_fixed_source_set_is_still_enforced():
    s=snapshot();s['sources'][0]['url']='https://api-docs.deepseek.com/other'
    with pytest.raises(ValueError):vs.verify_current_sources(s,lambda u:'x',cached=False,log=lambda t:None)


def test_the_background_watch_records_a_failed_check_and_stops_when_told():
    import threading
    stop=threading.Event();calls=[]
    class Gateways:
        def verify_vendor(self):
            calls.append(1);stop.set();raise RuntimeError('boom')
    cloud_runtime._watch(Gateways(),stop,0)
    assert calls==[1] and any(w['kind']=='vendor-stop' and 'boom' in w['text'] for w in vendor_status.status()['warnings'])


@pytest.mark.parametrize('hours',[24,72])
def test_stale_failed_refresh_blocks_even_if_warning_is_cached(monkeypatch,hours):
    from delivery_api.attempt_ledger import AdmissionDenied
    snap=snapshot(hours)
    vs._WARNED[vs.ProviderSnapshot(snap).id]=vs.time.monotonic()
    monkeypatch.setattr(vs,'fetch_verified_source',lambda *a:(_ for _ in ()).throw(ConnectionError('offline')))
    with pytest.raises(AdmissionDenied,match='24 hours'):
        vs.verify_current_sources(snap,cached=True)


def test_successful_refresh_accepts_old_pin_then_outage_uses_recent_verification(monkeypatch):
    snap=snapshot(72)
    run(snap,monkeypatch,parse=lambda *a:parsed())
    monkeypatch.setattr(vs,'fetch_verified_source',lambda *a:(_ for _ in ()).throw(ConnectionError('offline')))
    assert vs.verify_current_sources(snap,cached=False,log=lambda t:None)
