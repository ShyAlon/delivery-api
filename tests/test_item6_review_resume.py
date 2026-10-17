"""A run that got past tests and died inside review resumes at review; an extra top-up is never bought on a funded account."""
import re
from pathlib import Path

S=Path(__file__).resolve().parent.parent/'scripts/item6'


def test_checkpoint_review_pins_saved_review_chunks_only_after_tests_and_validation():
    t=(S/'recovery_generator.py').read_text()
    assert "review_part=out/'review.partial.json'" in t
    assert "(out/'tests.json').is_file() and (out/'validation.json').is_file() and not tests_part.is_file()" in t
    assert "'review_partials_present':True" in t and '**tests_extra,**review_extra}' in t


def test_executor_accepts_review_as_the_only_next_step_when_pinned():
    t=(S/'pipeline_executor.py').read_text()
    assert "expected_next=('review',) if generator_resume.get('review_partials_present') else ('tests',) if generator_resume.get('tests_partials_present') else ('architecture',)" in t
    assert "tuple(snapshot.next)!=expected_next" in t


def test_resume_entry_names_the_stage_it_resumes_and_refuses_when_neither_is_saved():
    t=(S/'resume_entry.py').read_text()
    assert "stage='review' if resume.get('review_partials_present') else 'tests' if resume.get('tests_partials_present') else None" in t
    assert 'saved tests fills or saved review chunks are required' in t
    assert "'resuming the generator from its saved '+stage+' checkpoint '" in t


def test_extra_top_up_is_skipped_when_the_balance_is_already_sufficient():
    t=(S/'resume_entry.py').read_text()
    assert re.search(r'^EXTRA_TOPUP_BELOW_CREDITS=10$',t,re.M)
    a=t.index("extra=topup_offer is not None");b=t.index("if extra and paths['number']==1")
    block=t[a:b]
    assert "facts['balance_units']>=EXTRA_TOPUP_BELOW_CREDITS*10**9" in block and 'extra=False' in block and 'the extra top-up is skipped' in block
