"""Public API schema enum rendering may change, not model identity or costs."""
from pathlib import Path
import pytest
from delivery_api import vendor_sources as vs

FIXTURES=Path(__file__).parent/'fixtures/vendor-pages'
PRICING=(FIXTURES/'page-280758cc.html').read_text()
OLD=(FIXTURES/'page-a4d0a95f.html').read_text()
CURRENT=(FIXTURES/'api-enum-unbracketed-2026-10-30.html').read_text()


def test_live_unbracketed_enum_preserves_every_previously_published_fact():
    before=vs.parse(PRICING,OLD,'deepseek-flash')
    after=vs.parse((FIXTURES/'pricing-enum-2026-10-30.html').read_text(),CURRENT,'deepseek-flash')
    assert after==before


@pytest.mark.parametrize('replacement',['deepseek-unknown','deepseek-flash-v2'])
def test_real_model_disagreement_still_refuses(replacement):
    changed=CURRENT.replace('deepseek-v4-pro',replacement)
    with pytest.raises(vs.SourceChanged,match='API model identities disagree'):
        vs.parse(PRICING,changed,'deepseek-flash')


@pytest.mark.parametrize('values',[
    '[ deepseek-flash , deepseek-v4-pro',
    'deepseek-flash , deepseek-v4-pro ]',
    'deepseek-flash , deepseek-v4-pro , deepseek-flash',
    'deepseek-flash , deepseek-v4-pro , deepseek-new',
    'deepseek-flash',
])
def test_malformed_missing_duplicate_or_added_identity_refuses(values):
    table=vs.Table();table.feed(CURRENT)
    text=' '.join(' '.join(table.article_text).split())
    changed=text.replace('model string required Possible values: deepseek-flash , deepseek-v4-pro ID',
        'model string required Possible values: '+values+' ID')
    with pytest.raises(vs.SourceChanged,match='API model identities disagree'):
        vs.parse(PRICING,'<article>'+changed+'</article>','deepseek-flash')


def test_two_model_fields_are_ambiguous_even_if_identical():
    table=vs.Table();table.feed(CURRENT)
    text=' '.join(' '.join(table.article_text).split())
    with pytest.raises(vs.SourceChanged,match='API model identities disagree'):
        vs.parse(PRICING,'<article>'+text+' '+text+'</article>','deepseek-flash')
