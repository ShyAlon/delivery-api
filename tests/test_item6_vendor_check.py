import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import datetime,hashlib
import pytest
import model_data
import vendor_config as vc
from delivery_api.provider_snapshot import RULE

RATES={'peak':{'cache_hit':'0.006','cache_miss':'0.3','output':'1.2'},'off_peak':{'cache_hit':'0.003','cache_miss':'0.15','output':'0.6'}}


def pricing_html(rates=RATES,extra_table=True,wording='Model prices are listed in units of per 1M tokens.',second_model_table=False):
    def row(label,a,b,span=None):
        return '<tr><td colspan="3">%s</td><td>%s</td><td>%s</td></tr>'%(label,a,b)
    def rate(label,key):
        return ''.join('<tr><td>PRICING</td><td>%s</td><td>%s</td><td>$%s</td><td>$2</td></tr>'%(label,period.upper().replace('_','-'),rates[period][key]) for period in ('peak','off_peak'))
    model='<table><tr><td colspan="3">MODEL</td><td>deepseek-flash (1)</td><td>deepseek-v4-pro</td></tr>'+row('MODEL VERSION','DeepSeek-V4.1-Flash','V4')+row('CONTEXT LENGTH','1M','1M')+row('MAX OUTPUT','MAXIMUM: 384K','MAXIMUM: 384K')+rate('1M INPUT TOKENS (CACHE HIT)','cache_hit')+rate('1M INPUT TOKENS (CACHE MISS)','cache_miss')+rate('1M OUTPUT TOKENS','output')+'</table>'
    other='<table><tr><td>API</td><td>Unit</td><td>Price</td></tr><tr><td>Search</td><td>Per 1,000</td><td>$0.75</td></tr></table>' if extra_table else ''
    if second_model_table:other+=model
    return '<html><article><p>%s</p>%s%s<p>Off-peak rates are half of the peak rates. %s</p></article><script src="main.abc123.js"></script></html>'%(wording,model,other,RULE)


API='<html><article>model string required Possible values: [ deepseek-flash, deepseek-v4-pro ] ID of the model to use. The value must be between 1 and 384K (393216)</article></html>'


def snapshot(pricing,api=API):
    facts=model_data.parse(pricing,api,'deepseek-flash')
    facts['parser_version']='deepseek-html-v2';facts['fetched_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    facts['sources']=[{'url':u,'sha256':hashlib.sha256(b.encode()).hexdigest()} for u,b in ((model_data.PRICING_URL,pricing),(model_data.API_URL,api))]
    facts['protective_run_context_tokens']=128000
    return facts


class Pages:
    def __init__(self,pricing,api=API):self.pages={model_data.PRICING_URL:pricing,model_data.API_URL:api};self.calls=0
    def __call__(self,url):self.calls+=1;return self.pages[url]


class Clock:
    def __init__(self):self.t=1000.0
    def __call__(self):return self.t


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    vc._GOOD.clear();vc._WARNED.clear();monkeypatch.delenv('VENDOR_CHECK_MAX_AGE_MINUTES',raising=False);yield;vc._GOOD.clear();vc._WARNED.clear()


def test_extra_table_and_new_wording_still_parse():
    facts=model_data.parse(pricing_html(),API,'deepseek-flash')
    assert facts['published_usd_per_million']==RATES


def test_two_model_tables_fail_closed():
    with pytest.raises(model_data.SourceChanged):model_data.parse(pricing_html(second_model_table=True),API,'deepseek-flash')


def test_identical_pages_pass_and_are_cached():
    pages=Pages(pricing_html());snap=snapshot(pricing_html());clock=Clock()
    assert vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None) is True
    assert pages.calls==2
    clock.t+=59*60
    assert vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None) is True
    assert pages.calls==2


def test_cache_expires_after_the_interval_and_uncached_always_reads():
    pages=Pages(pricing_html());snap=snapshot(pricing_html());clock=Clock()
    vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None)
    clock.t+=61*60
    vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None)
    assert pages.calls==4
    vc.verify_current_sources(snap,pages,cached=False,clock=clock,log=lambda t:None)
    assert pages.calls==6


def test_interval_setting_is_bounded(monkeypatch):
    for bad in ('0','61','x',''):
        monkeypatch.setenv('VENDOR_CHECK_MAX_AGE_MINUTES',bad)
        with pytest.raises(ValueError,match='VENDOR_CHECK_MAX_AGE_MINUTES'):vc.check_max_age_minutes()
    monkeypatch.setenv('VENDOR_CHECK_MAX_AGE_MINUTES','5');assert vc.check_max_age_minutes()==5


def test_changed_bytes_with_identical_terms_pass_and_log():
    snap=snapshot(pricing_html(extra_table=False,wording='prices listed below are in units of per 1M tokens'))
    lines=[];pages=Pages(pricing_html())
    assert vc.verify_current_sources(snap,pages,clock=Clock(),log=lines.append) is True
    assert 'every pinned term is identical' in lines[0] and lines[0][:4]=='2026'


def test_changed_price_fails_names_the_field_and_is_not_cached():
    snap=snapshot(pricing_html());clock=Clock()
    vc.verify_current_sources(snap,Pages(pricing_html()),clock=clock,log=lambda t:None)
    vc._GOOD.clear()
    higher={'peak':{'cache_hit':'0.006','cache_miss':'0.4','output':'1.2'},'off_peak':{'cache_hit':'0.003','cache_miss':'0.2','output':'0.6'}}
    pages=Pages(pricing_html(higher))
    with pytest.raises(ValueError,match='published_usd_per_million'):vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None)
    with pytest.raises(ValueError,match='published_usd_per_million'):vc.verify_current_sources(snap,pages,clock=clock,log=lambda t:None)
    assert pages.calls==4


def aged(snap,hours=3):
    snap['fetched_at']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=hours)).isoformat();return snap


def test_unparseable_model_table_fails_closed_with_the_last_good_time():
    snap=aged(snapshot(pricing_html()));clock=Clock()
    vc.verify_current_sources(snap,Pages(pricing_html()),clock=clock,log=lambda t:None)
    clock.t+=61*60
    with pytest.raises(ValueError,match='no longer parses.*last good check 20'):vc.verify_current_sources(snap,Pages(pricing_html(second_model_table=True)),clock=clock,log=lambda t:None)


VERSION_ONLY=lambda:pricing_html().replace('DeepSeek-V4.1-Flash','DeepSeek-V4.1-Flash-1017')


def test_a_version_only_change_inside_the_hour_is_a_warning_and_a_finding(tmp_path,monkeypatch):
    import json,findings
    monkeypatch.setenv('ITEM6_FINDINGS',str(tmp_path/'f.jsonl'))
    snap=snapshot(pricing_html());lines=[]
    assert vc.verify_current_sources(snap,Pages(VERSION_ONLY()),cached=False,clock=Clock(),log=lines.append) is True
    assert 'WARNING' in lines[0] and lines[0][:4]=='2026' and "'DeepSeek-V4.1-Flash' -> 'DeepSeek-V4.1-Flash-1017'" in lines[0] and 'run continues' in lines[0]
    rows=[json.loads(l) for l in (tmp_path/'f.jsonl').read_text().splitlines()]
    assert len(rows)==1 and rows[0]['kind']=='vendor-change' and 'model_version' in rows[0]['text']
    assert (tmp_path/'f.jsonl').stat().st_mode&0o077==0


def test_a_warning_is_not_a_good_check_and_is_reused_only_for_the_interval():
    snap=snapshot(pricing_html());pages=Pages(VERSION_ONLY());clock=Clock();lines=[]
    vc.verify_current_sources(snap,pages,clock=clock,log=lines.append)
    assert pages.calls==2 and not vc._GOOD
    vc.verify_current_sources(snap,pages,clock=clock,log=lines.append)
    assert pages.calls==2 and len(lines)==1
    clock.t+=61*60
    vc.verify_current_sources(snap,pages,clock=clock,log=lines.append)
    assert pages.calls==4 and len(lines)==2


def test_a_version_only_change_after_the_hour_still_stops():
    snap=aged(snapshot(pricing_html()))
    with pytest.raises(ValueError,match='model_version.*no earlier good check in this process'):vc.verify_current_sources(snap,Pages(VERSION_ONLY()),cached=False,clock=Clock(),log=lambda t:None)


def test_the_in_process_good_check_also_starts_the_grace_period():
    snap=aged(snapshot(pricing_html()));clock=Clock();lines=[]
    vc.verify_current_sources(snap,Pages(pricing_html()),clock=clock,log=lines.append)
    clock.t+=30*60
    vc._GOOD[vc.ProviderSnapshot(snap).id]=(clock.t-30*60,'t')
    assert vc.verify_current_sources(snap,Pages(VERSION_ONLY()),cached=False,clock=clock,log=lines.append) is True
    clock.t+=40*60;vc._WARNED.clear()
    with pytest.raises(ValueError,match='model_version'):vc.verify_current_sources(snap,Pages(VERSION_ONLY()),cached=False,clock=clock,log=lines.append)


@pytest.mark.parametrize('change',['price','limit','rule'])
def test_a_changed_price_limit_or_peak_rule_always_stops_even_inside_the_hour(change):
    snap=snapshot(pricing_html())
    if change=='price':html=pricing_html({'peak':{'cache_hit':'0.006','cache_miss':'0.4','output':'1.2'},'off_peak':{'cache_hit':'0.003','cache_miss':'0.2','output':'0.6'}})
    elif change=='limit':html=pricing_html().replace('>1M<','>2M<')
    else:html=pricing_html().replace('Monday through Friday','every day')
    with pytest.raises(ValueError,match='pinned terms differ|no longer parses|period'):vc.verify_current_sources(snap,Pages(html),cached=False,clock=Clock(),log=lambda t:None)


def test_an_unreadable_page_inside_the_hour_is_a_warning_after_the_hour_an_error(monkeypatch):
    def broken(url,fetcher=None):raise ConnectionError('network down')
    monkeypatch.setattr(vc,'fetch_verified_source',broken)
    lines=[]
    assert vc.verify_current_sources(snapshot(pricing_html()),Pages(pricing_html()),cached=False,clock=Clock(),log=lines.append) is True
    assert 'WARNING' in lines[0] and 'ConnectionError: network down' in lines[0]
    with pytest.raises(ConnectionError):vc.verify_current_sources(aged(snapshot(pricing_html())),Pages(pricing_html()),cached=False,clock=Clock(),log=lines.append)
