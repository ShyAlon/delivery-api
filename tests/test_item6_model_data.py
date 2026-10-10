"""Frozen public vendor excerpts and stubs only. No live requests or keys."""
import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from scaffold import scaffold,validate
from model_data import parse,propose,SourceChanged,PRICING_URL,API_URL,NoRedirect

FIX=Path(__file__).parent/'fixtures/item6-model-data'
P=(FIX/'pricing.html').read_text();A=(FIX/'api-limits.html').read_text()

@pytest.mark.parametrize('model,price',[('deepseek-flash','0.003'),('deepseek-v4-pro','0.022')])
def test_exact_source_identity_units_both_periods_and_review_holes(model,price):
    r=parse(P,A,model)
    assert r['reviewed'] is False and r['execution_config_changed'] is False
    assert r['published_usd_per_million']['off_peak']['cache_hit']==price
    assert r['context_proposal']['maximum_output_tokens']==393216
    assert r['context_proposal']['context_tokens'] is None
    assert r['raw_context_limit']=='1M'
    assert r['catalog_proposals']['off_peak'][0]['retail_input'] is None
    assert 'REPLACE' in r['catalog_proposals']['peak'][0]['price_version']
    rate=r['catalog_proposals']['off_peak'][0]
    from decimal import Decimal
    assert Decimal(rate['expense_cache_hit'])/rate['expense_units_per_usd']*1000000==Decimal(price)
    assert 'UTC' in r['published_period_rule']

@pytest.mark.parametrize('kind',['price','currency','period','output','context','table','model','schedule'])
def test_source_drift_fails_closed(kind):
    p=P;a=A;model='deepseek-flash'
    if kind=='price':p=p.replace('$0.003','unknown')
    if kind=='currency':p=p.replace('$0.003','€0.003')
    if kind=='period':p=p.replace('OFF-PEAK','OTHER')
    if kind=='output':a=a.replace('393216','123456').replace('384K','382K')
    if kind=='context':p=p.replace('1M</td>','unclear</td>')
    if kind=='table':p+=P
    if kind=='model':model='deepseek-chat'
    if kind=='schedule':p=p.replace('Peak hours are','Prices were')
    with pytest.raises(SourceChanged):parse(p,a,model)


def test_fetch_creates_proposal_only_original_image_remains_blocked(tmp_path):
    root=tmp_path/'run';scaffold(root)
    before={p.name:p.read_bytes() for p in (root/'private').iterdir()}
    calls=[]
    def fetch(url):calls.append(url);return P if url==PRICING_URL else A
    r=propose(root,'deepseek-flash',fetch)
    assert calls==[PRICING_URL,API_URL]
    assert r['execution_config_changed'] is False and r['run_started'] is False
    for name,data in before.items():assert (root/'private'/name).read_bytes()==data
    assert validate(root)['ready_for_dry_preflight'] is False
    p=root/'private/deepseek-proposal.json'
    assert p.stat().st_mode&0o777==0o600
    proposal=json.loads(p.read_text());assert proposal['reviewed'] is False
    assert 'sha256' in proposal['sources'][0]
    with pytest.raises(ValueError):propose(root,'deepseek-flash',fetch)


def test_bad_source_leaves_no_partial_proposal(tmp_path):
    root=tmp_path/'run';scaffold(root)
    with pytest.raises(SourceChanged):propose(root,'deepseek-chat',lambda url:P if url==PRICING_URL else A)
    assert not (root/'private/deepseek-proposal.json').exists()


def test_evidence_or_symlink_reuse_rejected_before_fetch(tmp_path):
    root=tmp_path/'run';scaffold(root);(root/'evidence/existing').write_text('old')
    with pytest.raises(ValueError):propose(root,'deepseek-flash',lambda url:pytest.fail('must not fetch'))
    link=tmp_path/'link';link.symlink_to(root,target_is_directory=True)
    with pytest.raises(ValueError):propose(link,'deepseek-flash',lambda url:pytest.fail('must not fetch'))


def test_redirects_are_not_followed():
    with pytest.raises(SourceChanged):NoRedirect().redirect_request(None,None,None,None,None,None)


def test_period_ratio_and_unit_contract_are_required():
    with pytest.raises(SourceChanged):parse(P.replace('$0.006','$0.007'),A,'deepseek-flash')
    with pytest.raises(SourceChanged):parse(P.replace('prices listed below are in units of per 1M tokens','prices are per request'),A,'deepseek-flash')


def test_proposal_does_not_read_credential_contents(tmp_path):
    root=tmp_path/'run';scaffold(root)
    credentials=root/'private/credentials.json';credentials.write_text('not JSON; secrets are irrelevant to public fetch')
    r=propose(root,'deepseek-v4-pro',lambda url:P if url==PRICING_URL else A)
    assert r['model']=='deepseek-v4-pro' and credentials.read_text().startswith('not JSON')
