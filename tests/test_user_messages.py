"""Customers read credits wording only: no vendor cost, spend limit or internal check name reaches them."""
from pathlib import Path

import pytest

from delivery_api import user_messages as um

SRC=Path(__file__).resolve().parents[1]/'src'/'delivery_api'
RAW=['owner spend allowance exhausted','attempt exceeds reviewed maximum','owner attempt rate exhausted','owner attempt concurrency exhausted',
     'reviewed owner admission configuration missing','uncertain provider usage requires review','unresolved compute accounting requires review',
     'release limit reached; the attempt stays held for review','conflicting admission replay']
FORBIDDEN=('expense','vendor','allowance','limit','reviewed','admission','attempt','AdmissionDenied')


@pytest.mark.parametrize('reason',RAW+['something nobody wrote a mapping for',''])
def test_every_raw_reason_becomes_a_message_without_internal_words(reason):
    text=um.user_denial_message(reason)
    assert text in (um.SAFETY,um.NEEDS_CREDITS)
    assert not any(word.lower() in text.lower() for word in FORBIDDEN)


def test_a_balance_problem_says_credits_and_a_spend_cap_does_not():
    assert um.user_denial_message('prepaid balance is not enough for this call')==um.NEEDS_CREDITS
    assert um.user_denial_message('owner spend allowance exhausted')==um.SAFETY


def test_failure_text_is_scrubbed_once_and_ordinary_lines_pass_untouched():
    text='Traceback (most recent call last):\n  File "x.py", line 3\nAdmissionDenied: owner spend allowance exhausted\nnote: keep this line'
    out=um.scrub_failure_text(text)
    assert 'allowance' not in out and 'AdmissionDenied' not in out and out.count(um.SAFETY)==1
    assert 'File "x.py", line 3' in out and 'note: keep this line' in out
    ordinary='Building the needs section.\nReviewed 12 items.\nThe attempt to parse failed, retrying.'
    assert um.scrub_failure_text(ordinary)==ordinary


def test_the_three_denial_responses_never_echo_the_raw_reason():
    app=(SRC/'app.py').read_text()
    assert app.count(':_customer_denial(exc')==3
    for marker in ('"error":str(exc),"next":"Stop remains pending','"error":str(exc),"next":"Worker completion and usage','"cause":str(exc),"next":"Open Debug'):
        assert marker not in app


def test_child_failure_envelope_retains_shape_without_secrets(capsys):
    from delivery_api.child_failure import emit,parse
    try:
        raise RuntimeError('raw provider credential and private source')
    except RuntimeError as exc:
        emit(exc)
    line=capsys.readouterr().err.encode()
    result=parse(line)
    assert result['error_class']=='RuntimeError' and result['frames']
    assert result['frames'][-1]['function']=='test_child_failure_envelope_retains_shape_without_secrets'
    assert 'raw provider' not in line.decode() and 'credential' not in line.decode()
    assert set(result)=={'error_class','frames'}
