"""The retired checkpoint transcript cannot override recorded event evidence."""
from types import SimpleNamespace
from delivery_api import app as service


def test_transcript_has_no_checkpoint_recovery_entrypoint():
    assert not hasattr(service,'_checkpoint_interview_turns')


def test_event_answers_and_route_switch_keep_their_original_order(monkeypatch):
    events=[
        {'id':1,'phase_key':'intent','kind':'waiting_input','message':'Q1','payload':{'input_id':'a'}},
        {'id':2,'phase_key':'intent','kind':'answer','message':'You: A1','payload':{'input_id':'a'}},
        {'id':3,'phase_key':'intent','kind':'model_route','message':'Switched model','payload':{'scope':'switch','route_version':2}},
        {'id':4,'phase_key':'intent','kind':'waiting_input','message':'Q2','payload':{'input_id':'b'}},
        {'id':5,'phase_key':'intent','kind':'answer','message':'You: A2','payload':{'input_id':'b'}}]
    monkeypatch.setattr(service,'_customer_events',lambda rows:rows)
    monkeypatch.setattr(service,'store',SimpleNamespace(events=lambda _:events))
    monkeypatch.setattr(service,'_interview_model_provenance',lambda *args:[])
    text=service._interview_transcript_markdown('task')
    assert text.index('Q1')<text.index('A1')<text.index('Switched model')<text.index('Q2')<text.index('A2')
    assert text.count('### Question ')==2


def test_length_only_historical_answer_is_not_invented(monkeypatch):
    events=[{'id':1,'phase_key':'intent','kind':'waiting_input','message':'Old question','payload':{}},
            {'id':2,'phase_key':'intent','kind':'answer','message':'Interview answer submitted','payload':{'character_count':10}}]
    monkeypatch.setattr(service,'_customer_events',lambda rows:rows)
    monkeypatch.setattr(service,'store',SimpleNamespace(events=lambda _:events))
    monkeypatch.setattr(service,'_interview_model_provenance',lambda *args:[])
    assert service._interview_transcript_markdown('task')==''
