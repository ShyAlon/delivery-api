import threading
from collections import deque
from types import SimpleNamespace
from delivery_api.child_interview import ChildInterviewClient
from delivery_api.tenancy import TenantScope,tenant_context

def test_bound_progress_rejects_cross_session_and_keeps_bounded_replay():
    import pytest
    from vmodel_contracts.progress_signal import ProgressSignal
    client=ChildInterviewClient.__new__(ChildInterviewClient)
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
    target={'sid':'s','task_id':'t','scope':scope,'cursor':1000,'events':deque(maxlen=2)}
    client._progress_lock=threading.Lock();client._progress_operations={'op':target};client._transient_progress={'s':target}
    def page(sid='s'):
        return ProgressSignal(event_id='event',cursor=1,task_id='t',session_id=sid,phase_key='intent',stage='probe',label='Probing',status='started').model_dump(mode='json')
    client._record_answer_progress({'operation_id':'other','page':page()})
    client._record_answer_progress({'operation_id':'op','page':page('foreign')})
    assert not target['events']
    for _ in range(3):client._record_answer_progress({'operation_id':'op','page':page()})
    with tenant_context(scope):
        result=client.progress('s',after=1000)
        assert [e.cursor for e in result.events]==[1002,1003]
        assert client.progress('s',after=1003).next_cursor==1003


def test_answer_timings_buffer_without_database_and_legacy_unchanged():
    from unittest.mock import Mock
    c=ChildInterviewClient.__new__(ChildInterviewClient)
    c._progress_lock=threading.Lock()
    target={'sid':'s','timings':deque(maxlen=128)}
    c._progress_operations={'op':target};c.store=Mock()
    c.store.add_engine_timing_batch.side_effect=AssertionError('answer timing must not write DB')
    c._record_engine_timings([('s','engine.turn','Thinking','started'),('s','engine.turn','Thought','completed')])
    assert len(target['timings'])==2
    c.store.add_engine_timing_batch.assert_not_called()
    c.store.add_engine_timing_batch.side_effect=None
    c._record_engine_timings([('legacy','engine.turn','Thinking','started')])
    c.store.add_engine_timing_batch.assert_called_once()
