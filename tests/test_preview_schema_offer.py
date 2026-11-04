"""The preview offers the existing guarded runner, never a second SQL path."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]

def module():
    spec=importlib.util.spec_from_file_location('offer_readiness',ROOT/'scripts/ops/schema-readiness.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.parametrize('status',[0,2])
def test_ready_or_unavailable_never_offers(status):
    assert module().review_and_recheck(lambda:status,lambda:pytest.fail('unexpected apply'))==status

@pytest.mark.parametrize('result',[1,2])
def test_decline_or_failure_stops_without_recheck(result):
    calls=[]
    assert module().review_and_recheck(lambda:calls.append('check') or 1,lambda:result)==result
    assert calls==['check']

def test_success_rechecks_and_still_fails_when_not_ready():
    for final in (0,1,2):
        statuses=iter([1,final]);calls=[]
        assert module().review_and_recheck(lambda:next(statuses),lambda:calls.append('guarded apply') or 0)==final
        assert calls==['guarded apply']

def test_wiring_keeps_exact_existing_guards_and_separate_setup():
    owner=(ROOT/'scripts/owner/apply-pg-cutover.py').read_text()
    assert owner.index('if args.preview_offer:return 0')<owner.index('operator_setup=module(')
    readiness=(ROOT/'scripts/ops/schema-readiness.py').read_text()
    assert "'--apply','--review-projection-functions','--preview-offer'" in readiness
    preview=(ROOT/'scripts/docker/local_image_preview.py').read_text()
    assert 'schema-readiness.py --offer-apply' not in preview and 'apply-pg-cutover' not in preview
