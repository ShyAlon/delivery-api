"""Focused guards for the crosshost test harness (parent-approved harness
corrections, 2026-10-23):
1. The crosshost stub derives its probing reply from the supplied message
   history, so an uninterrupted state and a restarted state return the
   identical next response for the same history.
2. The UI driver's turn tracker answers each engine input_id exactly once,
   even when question text repeats with a new input_id.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

STUB_DIR = Path(__file__).resolve().parent / 'crosshost_stub'
ENGINE_SRC = Path(os.environ['DELIVERY_TEST_ENGINE_SOURCE']) if os.environ.get('DELIVERY_TEST_ENGINE_SOURCE') else None

_FIDELITY_SCRIPT = r'''
import json, sys
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import sitecustomize as stub
from interview_engine import model_config

def history(rounds):
    # One engine turn appends exactly one AI message (the asked question).
    msgs = [SystemMessage(content='probe rules')]
    for i in range(rounds):
        msgs.append(AIMessage(content='Q%d' % (i + 1)))
        msgs.append(HumanMessage(content='A%d' % (i + 1)))
    msgs.append(HumanMessage(content='contract'))
    return msgs

model = model_config.build_chat_model('probing')
uninterrupted = [model.invoke(history(n)).content for n in range(4)]

stub._box.clear()  # simulated host restart: fresh process-local stub state
model2 = model_config.build_chat_model('probing')
restarted = [model2.invoke(history(n)).content for n in range(4)]

assert restarted == uninterrupted, (uninterrupted, restarted)
qs = [json.loads(c)['question'] for c in uninterrupted]
assert qs == [
    'Walk me through one concrete use case, part 1?',
    'Walk me through one concrete use case, part 2?',
    'Walk me through one concrete use case, part 3?',
    'Anything else to add before we wrap up?',
], qs
print('STUB-FIDELITY-OK')
'''


@pytest.mark.skipif(ENGINE_SRC is None, reason='DELIVERY_TEST_ENGINE_SOURCE not set')
def test_stub_restart_returns_identical_response_for_same_history():
    env = dict(os.environ)
    env['CROSSHOST_STUB'] = '1'
    env['PYTHONPATH'] = str(STUB_DIR) + os.pathsep + str(ENGINE_SRC / 'src')
    proc = subprocess.run(
        [sys.executable, '-c', _FIDELITY_SCRIPT],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert 'STUB-FIDELITY-OK' in proc.stdout


def test_turn_tracker_answers_each_input_id_once_not_each_question_text():
    from test_crosshost_interview_acceptance import TurnTracker

    tracker = TurnTracker()
    # Same input_id seen twice (input_state served repeatedly before submit): one answer.
    assert tracker.should_answer('id-1')
    assert not tracker.should_answer('id-1')
    # Repeated question text with distinct input_ids: each is its own input, answered once each.
    assert tracker.should_answer('id-2')
    assert not tracker.should_answer('id-2')
    # Missing input_id: never treated as a new turn.
    assert not tracker.should_answer(None)
    assert not tracker.should_answer('')
