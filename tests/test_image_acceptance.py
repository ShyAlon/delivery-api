import importlib.util
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker'
sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('image_acceptance',D/'image_acceptance.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.mark.parametrize('base',['https://public.invalid','http://localhost:8001','http://127.0.0.1:8001/path','http://u@127.0.0.1:8001','http://127.0.0.1'])
def test_fixture_entry_refuses_nonexplicit_loopback(base):
    with pytest.raises(m.AcceptanceFailure):m.API(base,'fictional')


def fixture(*,stop=True,sequence=True):
    calls=[]
    def api(method,path,body=None,**kwargs):
        calls.append((method,path,body))
        if path=='/api/tasks' and method=='POST':return {'task_id':'fixture'}
        if path.endswith('/cost'):return {'evidence':{'complete':True}}
        if path.endswith('/stop'):return {'state':'stopped' if stop else 'working','delete_allowed':stop}
        return {'task_id':'fixture'}
    def wait(api,tid,**kwargs):
        return {'control_snapshot':{'input_id':'second' if kwargs.get('after') else 'first','precondition':{'session_sequence':3 if sequence else None}}}
    return api,wait,calls


def test_current_input_protocol_then_settled_stop_and_retained_read():
    api,wait,calls=fixture()
    result=m.http_slice(api,wait=wait)
    assert result['interview-start-answer-stop']=='passed'
    answer=next(body for method,path,body in calls if path.endswith('/answer'))
    assert answer['decision_id']=='first' and answer['expected_sequence']==3
    start=next(body for method,path,body in calls if path.endswith('/start'))
    assert start=={'model_route':'ollama'}
    assert calls[-1][:2]==('GET','/api/tasks/fixture')
    assert not any('/purge' in path for _,path,_ in calls)


def test_missing_sequence_never_answers_or_stops():
    api,wait,calls=fixture(sequence=False)
    with pytest.raises(m.AcceptanceFailure,match='sequence'):m.http_slice(api,wait=wait)
    assert not any(path.endswith(('/answer','/stop')) for _,path,_ in calls)


def test_unconfirmed_stop_never_passes():
    api,wait,calls=fixture(stop=False)
    with pytest.raises(m.AcceptanceFailure,match='Stop'):m.http_slice(api,wait=wait)
