"""Hermetic checks of the fixed-origin transports and the vendor source reader. No network."""
import httpx
import pytest

from delivery_api import vendor_sources
from delivery_api.provider_network import FixedOriginTransport

ORIGIN='https://api.deepseek.com'


def client(handler,allowed=lambda method,path:method=='POST' and path=='/chat/completions'):
    return httpx.Client(transport=FixedOriginTransport(httpx.MockTransport(handler),ORIGIN,allowed),trust_env=False,follow_redirects=False)


def test_allowed_request_passes():
    c=client(lambda r:httpx.Response(200,json={'ok':1}))
    assert c.post(ORIGIN+'/chat/completions',json={}).status_code==200


@pytest.mark.parametrize('method,url',[
    ('POST','https://evil.example/chat/completions'),
    ('POST','http://api.deepseek.com/chat/completions'),
    ('POST','https://api.deepseek.com:8443/chat/completions'),
    ('GET','https://api.deepseek.com/chat/completions'),
    ('POST','https://api.deepseek.com/other'),
    ('POST','https://user:pw@api.deepseek.com/chat/completions'),
])
def test_wrong_origin_method_path_or_credentials_refused_before_sending(method,url):
    sent=[]
    c=client(lambda r:sent.append(r) or httpx.Response(200))
    with pytest.raises(ValueError):c.request(method,url)
    assert sent==[]


def test_redirect_refused():
    c=client(lambda r:httpx.Response(302,headers={'location':'https://evil.example/'}))
    with pytest.raises(ValueError):c.post(ORIGIN+'/chat/completions',json={})


def test_vendor_fetch_only_reads_the_two_fixed_pages():
    for url in ('https://example.com/','http://api-docs.deepseek.com/quick_start/pricing/'):
        with pytest.raises(vendor_sources.SourceChanged):vendor_sources.fetch(url)


def test_vendor_check_requires_the_fixed_source_set():
    from delivery_api.provider_snapshot import RULE
    snap={'model':'m','model_version':'v','parser_version':'deepseek-html-v2','published_period_rule':RULE,
        'fetched_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'sources':[{'url':'https://api-docs.deepseek.com/a','sha256':'a'*64},{'url':'https://api-docs.deepseek.com/b','sha256':'b'*64}],
        'published_usd_per_million':{p:{'cache_hit':'1','cache_miss':'1','output':'1'} for p in ('peak','off_peak')}}
    with pytest.raises(ValueError):vendor_sources.verify_current_sources(snap,fetcher=lambda u:'x',cached=False,log=lambda t:None)


def test_vendor_check_age_setting_is_bounded(monkeypatch):
    for bad in ('0','61','x'):
        monkeypatch.setenv('VENDOR_CHECK_MAX_AGE_MINUTES',bad)
        with pytest.raises(ValueError):vendor_sources.check_max_age_minutes()
    monkeypatch.delenv('VENDOR_CHECK_MAX_AGE_MINUTES')
    assert vendor_sources.check_max_age_minutes()==60
